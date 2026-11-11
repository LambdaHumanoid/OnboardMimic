#include <array>
#include <limits>

#include <gtest/gtest.h>

#include "../include/lower_body_braking_priority_slew.hpp"

namespace lbps = lower_body_braking_priority_slew;

namespace {

struct Inputs {
  std::array<double, lbps::kJointCount> q_target{};
  std::array<double, lbps::kJointCount> q{};
  std::array<double, lbps::kJointCount> dq{};
  std::array<double, lbps::kJointCount> kp{};
  std::array<double, lbps::kJointCount> kd{};
  std::array<double, lbps::kJointCount> dq_target{};
  std::array<double, lbps::kJointCount> tau_ff{};

  Inputs() {
    kp.fill(100.0);
    kd.fill(2.0);
  }
};

lbps::Config EnabledConfig() {
  lbps::Config config;
  config.enabled = true;
  config.max_positive_power_target_step_rad = 0.2;
  config.hold_ticks = 2;
  return config;
}

lbps::Result Apply(
    lbps::Limiter& limiter,
    const Inputs& in,
    bool trigger = false,
    bool enforce = true) {
  return limiter.Apply(
      in.q_target, in.q, in.dq, in.kp, in.kd, in.dq_target,
      in.tau_ff, trigger, enforce);
}

}  // namespace

TEST(LowerBodyBrakingPrioritySlew, DisabledIsExactPassThrough) {
  lbps::Limiter limiter;
  Inputs in;
  in.q_target[3] = 1.0;
  in.dq[3] = 2.0;

  const auto first = Apply(limiter, in);
  in.q_target[3] = 2.0;
  const auto result = Apply(limiter, in, true);

  ASSERT_TRUE(first.valid);
  ASSERT_TRUE(result.valid);
  EXPECT_FALSE(result.active);
  EXPECT_EQ(result.applied_q_target, in.q_target);
  EXPECT_EQ(result.intervention_count, 0U);
}

TEST(LowerBodyBrakingPrioritySlew, LimitsPositivePowerJumpOnTriggerTick) {
  lbps::Limiter limiter(EnabledConfig());
  Inputs in;
  in.q_target[3] = 0.1;
  Apply(limiter, in);  // Establish the actually-sent target.

  in.q_target[3] = 1.0;
  in.dq[3] = 1.0;  // Raw PD torque and power are positive.
  const auto result = Apply(limiter, in, true);

  ASSERT_TRUE(result.valid);
  EXPECT_TRUE(result.triggered);
  EXPECT_TRUE(result.active);
  EXPECT_EQ(result.hold_ticks_remaining, 2U);
  EXPECT_NEAR(result.applied_q_target[3], 0.3, 1e-12);
  EXPECT_EQ(result.intervention_mask[3], 1U);
  EXPECT_EQ(result.intervention_count, 1U);
}

TEST(LowerBodyBrakingPrioritySlew, BrakingJumpPassesImmediately) {
  lbps::Limiter limiter(EnabledConfig());
  Inputs in;
  in.q_target[3] = 0.1;
  Apply(limiter, in);

  in.q_target[3] = -1.0;
  in.dq[3] = 1.0;  // Negative requested torque at positive dq: braking.
  const auto result = Apply(limiter, in, true);

  ASSERT_TRUE(result.valid);
  EXPECT_TRUE(result.active);
  EXPECT_LT(result.raw_power_w[3], 0.0);
  EXPECT_DOUBLE_EQ(result.applied_q_target[3], -1.0);
  EXPECT_EQ(result.intervention_mask[3], 0U);
}

TEST(LowerBodyBrakingPrioritySlew,
     EnergyReducingChangePassesWhenTotalPowerRemainsPositive) {
  lbps::Limiter limiter(EnabledConfig());
  Inputs in;
  in.q_target[9] = 2.21;
  Apply(limiter, in);  // Establish the actually-sent target.

  // Regression for the recorded right-knee failure: lowering the target from
  // 2.21 to 1.93 rad reduces positive torque at positive dq, but the resulting
  // total PD command is still positive power.  It must not be slew limited.
  in.q_target[9] = 1.93;
  in.q[9] = 1.0;
  in.dq[9] = 0.2;
  const auto result = Apply(limiter, in, true);

  ASSERT_TRUE(result.valid);
  EXPECT_TRUE(result.active);
  EXPECT_GT(result.raw_power_w[9], 0.0);
  EXPECT_DOUBLE_EQ(result.applied_q_target[9], 1.93);
  EXPECT_EQ(result.intervention_mask[9], 0U);
  EXPECT_EQ(result.intervention_count, 0U);
}

TEST(LowerBodyBrakingPrioritySlew, InterventionDoesNotAccumulateTargetLag) {
  lbps::Limiter limiter(EnabledConfig());
  Inputs in;
  Apply(limiter, in);

  in.q_target[9] = 1.0;
  in.dq[9] = 1.0;
  const auto clipped = Apply(limiter, in, true);
  EXPECT_NEAR(clipped.applied_q_target[9], 0.2, 1e-12);
  EXPECT_EQ(clipped.intervention_count, 1U);

  // The raw policy target is unchanged, so its new innovation is zero.  A
  // recursive slew would keep walking 0.2 -> 0.4 and accumulate phase lag;
  // the non-recursive guard must immediately return to the raw trajectory.
  const auto next = Apply(limiter, in);
  EXPECT_DOUBLE_EQ(next.applied_q_target[9], 1.0);
  EXPECT_EQ(next.intervention_count, 0U);
}

TEST(LowerBodyBrakingPrioritySlew, HipAndSmallKneeChangesPass) {
  lbps::Limiter limiter(EnabledConfig());
  Inputs in;
  Apply(limiter, in);

  in.q_target[0] = 1.0;   // Hip is deliberately outside the monitored set.
  in.q_target[10] = 0.19;  // Monitored but below the configured step.
  in.dq[0] = 1.0;
  in.dq[10] = 1.0;
  const auto result = Apply(limiter, in, true);

  ASSERT_TRUE(result.valid);
  EXPECT_DOUBLE_EQ(result.applied_q_target[0], 1.0);
  EXPECT_DOUBLE_EQ(result.applied_q_target[10], 0.19);
  EXPECT_EQ(result.intervention_count, 0U);
}

TEST(LowerBodyBrakingPrioritySlew, GateExpiresWithoutContinuousExtension) {
  lbps::Limiter limiter(EnabledConfig());
  Inputs in;
  Apply(limiter, in);
  in.q_target[3] = 1.0;
  in.dq[3] = 1.0;

  const auto first = Apply(limiter, in, true);
  const auto second = Apply(limiter, in, true);
  const auto expired = Apply(limiter, in);

  EXPECT_TRUE(first.triggered);
  EXPECT_EQ(first.hold_ticks_remaining, 2U);
  EXPECT_FALSE(second.triggered);
  EXPECT_TRUE(second.active);
  EXPECT_EQ(second.hold_ticks_remaining, 1U);
  EXPECT_FALSE(expired.active);
  EXPECT_DOUBLE_EQ(expired.applied_q_target[3], in.q_target[3]);
}

TEST(LowerBodyBrakingPrioritySlew, StartupBypassResetsGateAndTracksTarget) {
  lbps::Limiter limiter(EnabledConfig());
  Inputs in;
  in.q_target[3] = 0.5;
  Apply(limiter, in, true);
  const auto bypass = Apply(limiter, in, false, false);

  in.q_target[3] = 0.6;
  in.dq[3] = 1.0;
  const auto normal = Apply(limiter, in);

  EXPECT_FALSE(bypass.active);
  EXPECT_FALSE(normal.active);
  EXPECT_DOUBLE_EQ(normal.applied_q_target[3], 0.6);
}

TEST(LowerBodyBrakingPrioritySlew, RejectsInvalidConfigAndNonFiniteInput) {
  lbps::Config config = EnabledConfig();
  config.max_positive_power_target_step_rad = 0.0;
  lbps::Limiter invalid;
  EXPECT_FALSE(invalid.SetConfig(config));
  EXPECT_FALSE(Apply(invalid, Inputs{}).valid);

  lbps::Limiter limiter(EnabledConfig());
  Inputs in;
  in.dq[3] = std::numeric_limits<double>::infinity();
  EXPECT_FALSE(Apply(limiter, in).valid);
}
