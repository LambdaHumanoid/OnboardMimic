#include <array>
#include <limits>

#include <gtest/gtest.h>

#include "../include/lower_body_energy_governor.hpp"

namespace lbeg = lower_body_energy_governor;

namespace {

struct Inputs {
  std::array<double, lbeg::kJointCount> q_target{};
  std::array<double, lbeg::kJointCount> q{};
  std::array<double, lbeg::kJointCount> dq{};
  std::array<double, lbeg::kJointCount> kp{};
  std::array<double, lbeg::kJointCount> kd{};
  std::array<double, lbeg::kJointCount> dq_target{};
  std::array<double, lbeg::kJointCount> tau_ff{};

  Inputs() {
    kp.fill(100.0);
    kd.fill(2.0);
  }
};

lbeg::Result Apply(lbeg::Governor& governor, const Inputs& in,
                   bool enforce = true,
                   bool external_tremor_trigger = false) {
  return governor.Apply(in.q_target, in.q, in.dq, in.kp, in.kd,
                        in.dq_target, in.tau_ff, enforce,
                        external_tremor_trigger);
}

lbeg::Config EnabledConfig() {
  lbeg::Config config;
  config.enabled = true;
  config.dt = 0.02;
  config.effort_limits_nm.fill(1000.0);
  config.positive_power_limits_w.fill(10.0);
  config.positive_work_limits_j.fill(10.0);
  return config;
}

}  // namespace

TEST(LowerBodyEnergyGovernor, DisabledIsExactPassThrough) {
  lbeg::Governor governor;
  Inputs in;
  in.q_target[0] = 1.25;
  in.q[0] = -0.5;
  in.dq[0] = 7.0;

  const auto result = Apply(governor, in);

  ASSERT_TRUE(result.valid);
  EXPECT_FALSE(result.active);
  EXPECT_EQ(result.applied_q_target, in.q_target);
  EXPECT_EQ(result.intervention_count, 0U);
}

TEST(LowerBodyEnergyGovernor, EffortEnvelopeAppliesInBothDirections) {
  auto config = EnabledConfig();
  config.effort_limits_nm.fill(20.0);
  lbeg::Governor governor(config);
  Inputs positive;
  positive.q_target[0] = 1.0;  // +100 Nm at zero velocity.

  const auto positive_result = Apply(governor, positive);
  ASSERT_TRUE(positive_result.valid);
  EXPECT_NEAR(positive_result.applied_torque_nm[0], 20.0, 1e-12);
  EXPECT_NEAR(positive_result.applied_q_target[0], 0.2, 1e-12);
  EXPECT_EQ(positive_result.intervention_mask[0] & lbeg::kEffort,
            lbeg::kEffort);

  Inputs negative;
  negative.q_target[0] = -1.0;
  const auto negative_result = Apply(governor, negative);
  ASSERT_TRUE(negative_result.valid);
  EXPECT_NEAR(negative_result.applied_torque_nm[0], -20.0, 1e-12);
  EXPECT_NEAR(negative_result.applied_q_target[0], -0.2, 1e-12);
}

TEST(LowerBodyEnergyGovernor, CapsOnlyEnergyInjectingPower) {
  lbeg::Governor governor(EnabledConfig());
  Inputs in;
  in.dq[0] = 2.0;
  // tau = 100*(1-0) + 2*(0-2) = 96 Nm; requested power = 192 W.
  in.q_target[0] = 1.0;

  const auto result = Apply(governor, in);

  ASSERT_TRUE(result.valid);
  EXPECT_NEAR(result.applied_positive_power_w[0], 10.0, 1e-12);
  EXPECT_NEAR(result.applied_torque_nm[0], 5.0, 1e-12);
  EXPECT_EQ(result.intervention_mask[0] & lbeg::kPositivePower,
            lbeg::kPositivePower);
  // qref reconstructed so the same PD law requests +5 Nm.
  EXPECT_NEAR(result.applied_q_target[0], 0.09, 1e-12);
}

TEST(LowerBodyEnergyGovernor, BrakingPassesWithoutPowerOrWorkDelay) {
  auto config = EnabledConfig();
  config.positive_work_limits_j.fill(0.001);
  lbeg::Governor governor(config);
  Inputs in;
  in.dq[0] = 2.0;
  in.q_target[0] = -1.0;  // Negative torque while dq is positive: braking.

  // Fill the work history of other ticks/joints first; braking must still pass.
  Inputs injecting;
  injecting.dq[0] = 1.0;
  injecting.q_target[0] = 1.0;
  for (std::size_t i = 0; i < lbeg::kWindowSamples; ++i) {
    Apply(governor, injecting);
  }
  const auto result = Apply(governor, in);

  ASSERT_TRUE(result.valid);
  EXPECT_DOUBLE_EQ(result.applied_q_target[0], in.q_target[0]);
  EXPECT_DOUBLE_EQ(result.applied_torque_nm[0],
                   100.0 * (-1.0) + 2.0 * (-2.0));
  EXPECT_EQ(result.intervention_mask[0], lbeg::kNone);
}

TEST(LowerBodyEnergyGovernor, SlidingWorkWindowRestrictsLaterInjection) {
  auto config = EnabledConfig();
  config.positive_power_limits_w.fill(1000.0);
  config.positive_work_limits_j.fill(0.4);
  lbeg::Governor governor(config);
  Inputs in;
  in.dq[0] = 1.0;
  in.q_target[0] = 1.0;  // Raw tau=98 Nm.

  const auto first = Apply(governor, in);
  ASSERT_TRUE(first.valid);
  EXPECT_NEAR(first.applied_torque_nm[0], 20.0, 1e-12);  // 0.4 J / .02 s.
  EXPECT_EQ(first.intervention_mask[0] & lbeg::kPositiveWork,
            lbeg::kPositiveWork);

  const auto second = Apply(governor, in);
  ASSERT_TRUE(second.valid);
  EXPECT_NEAR(second.applied_torque_nm[0], 0.0, 1e-12);
  EXPECT_EQ(second.intervention_mask[0] & lbeg::kPositiveWork,
            lbeg::kPositiveWork);
}

TEST(LowerBodyEnergyGovernor, ResetClearsSlidingWorkHistory) {
  auto config = EnabledConfig();
  config.positive_power_limits_w.fill(1000.0);
  config.positive_work_limits_j.fill(0.4);
  lbeg::Governor governor(config);
  Inputs in;
  in.dq[0] = 1.0;
  in.q_target[0] = 1.0;

  Apply(governor, in);
  EXPECT_NEAR(Apply(governor, in).applied_torque_nm[0], 0.0, 1e-12);
  governor.Reset();
  EXPECT_NEAR(Apply(governor, in).applied_torque_nm[0], 20.0, 1e-12);
}

TEST(LowerBodyEnergyGovernor, StartupPassThroughResetsHistory) {
  auto config = EnabledConfig();
  config.positive_power_limits_w.fill(1000.0);
  config.positive_work_limits_j.fill(0.4);
  lbeg::Governor governor(config);
  Inputs in;
  in.dq[0] = 1.0;
  in.q_target[0] = 1.0;

  Apply(governor, in);
  const auto passthrough = Apply(governor, in, false);
  EXPECT_FALSE(passthrough.active);
  EXPECT_DOUBLE_EQ(passthrough.applied_q_target[0], in.q_target[0]);
  EXPECT_NEAR(Apply(governor, in).applied_torque_nm[0], 20.0, 1e-12);
}

TEST(LowerBodyEnergyGovernor, NoInterventionPreservesTargetExactly) {
  lbeg::Governor governor(EnabledConfig());
  Inputs in;
  in.q_target[3] = 0.123456789012345;
  in.dq[3] = 0.0;

  const auto result = Apply(governor, in);

  ASSERT_TRUE(result.valid);
  EXPECT_DOUBLE_EQ(result.applied_q_target[3], in.q_target[3]);
  EXPECT_EQ(result.intervention_mask[3], lbeg::kNone);
}

TEST(LowerBodyEnergyGovernor, RecoveryDampingIsDissipativeAndKneeAnkleOnly) {
  auto config = EnabledConfig();
  config.tremor_recovery_damping_nm_per_rad_s = 5.0;
  lbeg::Governor governor(config);
  Inputs in;
  in.dq[0] = 2.0;  // Hip is deliberately outside the recovery-damping set.
  in.dq[3] = 2.0;

  const auto result = Apply(governor, in);

  ASSERT_TRUE(result.valid);
  EXPECT_DOUBLE_EQ(result.applied_torque_nm[0], -4.0);
  EXPECT_EQ(result.intervention_mask[0], lbeg::kNone);
  EXPECT_NEAR(result.applied_torque_nm[3], -14.0, 1e-12);
  EXPECT_LT(result.applied_torque_nm[3] * in.dq[3], 0.0);
  EXPECT_EQ(result.intervention_mask[3] & lbeg::kRecoveryDamping,
            lbeg::kRecoveryDamping);
  EXPECT_NEAR(result.applied_q_target[3], -0.1, 1e-12);
}

TEST(LowerBodyEnergyGovernor, RejectsNonFiniteInputsAndInvalidConfig) {
  auto config = EnabledConfig();
  config.envelope_scale = 0.0;
  lbeg::Governor invalid_governor;
  EXPECT_FALSE(invalid_governor.SetConfig(config));
  EXPECT_FALSE(Apply(invalid_governor, Inputs{}).valid);

  config = EnabledConfig();
  config.tremor_recovery_damping_nm_per_rad_s = -1.0;
  EXPECT_FALSE(invalid_governor.SetConfig(config));

  lbeg::Governor governor(EnabledConfig());
  Inputs in;
  in.dq[5] = std::numeric_limits<double>::infinity();
  EXPECT_FALSE(Apply(governor, in).valid);
}

TEST(LowerBodyEnergyGovernor, TremorGateNeedsSameJointOppositeInnovations) {
  auto config = EnabledConfig();
  config.tremor_gate_enabled = true;
  config.tremor_trigger_threshold_rad_s = 4.0;
  config.tremor_peak_to_peak_threshold_rad_s = 9.0;
  lbeg::Governor governor(config);
  Inputs in;

  const auto priming = Apply(governor, in);
  ASSERT_TRUE(priming.valid);
  EXPECT_FALSE(priming.active);

  in.dq[3] = 5.0;
  const auto one_sided = Apply(governor, in);
  ASSERT_TRUE(one_sided.valid);
  EXPECT_FALSE(one_sided.active);
  EXPECT_FALSE(one_sided.tremor_gate_triggered);

  // An opposite innovation on another monitored joint must not cross-trigger.
  in.dq[4] = -5.0;
  const auto different_joint = Apply(governor, in);
  ASSERT_TRUE(different_joint.valid);
  EXPECT_FALSE(different_joint.active);
  EXPECT_FALSE(different_joint.tremor_gate_triggered);
}

TEST(LowerBodyEnergyGovernor, TremorGateActivatesBoundedRecoveryEnvelope) {
  auto config = EnabledConfig();
  config.tremor_gate_enabled = true;
  config.tremor_trigger_threshold_rad_s = 4.0;
  config.tremor_peak_to_peak_threshold_rad_s = 9.0;
  config.tremor_hold_ticks = 2;
  lbeg::Governor governor(config);
  Inputs in;

  Apply(governor, in);  // Establish the measured-velocity baseline.
  in.dq[3] = 5.0;
  EXPECT_FALSE(Apply(governor, in).active);

  in.dq[3] = -1.0;  // -6 rad/s innovation, opposite to the preceding +5.
  in.q_target[3] = -1.0;  // Energy-injecting torque at negative velocity.
  const auto trigger = Apply(governor, in);
  ASSERT_TRUE(trigger.valid);
  EXPECT_TRUE(trigger.tremor_gate_enabled);
  EXPECT_TRUE(trigger.tremor_gate_triggered);
  EXPECT_TRUE(trigger.tremor_gate_active);
  EXPECT_TRUE(trigger.active);
  EXPECT_EQ(trigger.tremor_gate_hold_ticks_remaining, 2U);
  EXPECT_GT(trigger.intervention_count, 0U);

  const auto held = Apply(governor, in);
  ASSERT_TRUE(held.valid);
  EXPECT_FALSE(held.tremor_gate_triggered);
  EXPECT_TRUE(held.active);
  EXPECT_EQ(held.tremor_gate_hold_ticks_remaining, 1U);

  const auto expired = Apply(governor, in);
  ASSERT_TRUE(expired.valid);
  EXPECT_FALSE(expired.active);
  EXPECT_FALSE(expired.tremor_gate_active);
  EXPECT_DOUBLE_EQ(expired.applied_q_target[3], in.q_target[3]);
}

TEST(LowerBodyEnergyGovernor, ExternalImpactTriggersEnvelopeOnSameTick) {
  auto config = EnabledConfig();
  config.tremor_gate_enabled = true;
  config.tremor_hold_ticks = 2;
  lbeg::Governor governor(config);
  Inputs in;
  in.dq[11] = 2.0;
  in.q_target[11] = 1.0;  // Energy-injecting command.

  const auto trigger = Apply(governor, in, true, true);
  ASSERT_TRUE(trigger.valid);
  EXPECT_TRUE(trigger.tremor_gate_triggered);
  EXPECT_TRUE(trigger.tremor_gate_active);
  EXPECT_TRUE(trigger.active);
  EXPECT_EQ(trigger.tremor_gate_hold_ticks_remaining, 2U);
  EXPECT_GT(trigger.intervention_count, 0U);

  // A repeated detector sample inside the recovery window must not extend it.
  const auto held = Apply(governor, in, true, true);
  ASSERT_TRUE(held.valid);
  EXPECT_FALSE(held.tremor_gate_triggered);
  EXPECT_TRUE(held.active);
  EXPECT_EQ(held.tremor_gate_hold_ticks_remaining, 1U);

  const auto expired = Apply(governor, in);
  ASSERT_TRUE(expired.valid);
  EXPECT_FALSE(expired.active);
  EXPECT_DOUBLE_EQ(expired.applied_q_target[11], in.q_target[11]);
}

TEST(LowerBodyEnergyGovernor, ExternalImpactGateStillPassesBraking) {
  auto config = EnabledConfig();
  config.tremor_gate_enabled = true;
  config.tremor_hold_ticks = 2;
  config.positive_work_limits_j.fill(0.001);
  lbeg::Governor governor(config);
  Inputs in;
  in.dq[11] = 2.0;
  in.q_target[11] = -1.0;  // Negative torque at positive speed is braking.

  const auto result = Apply(governor, in, true, true);
  ASSERT_TRUE(result.valid);
  EXPECT_TRUE(result.active);
  EXPECT_TRUE(result.tremor_gate_triggered);
  EXPECT_DOUBLE_EQ(result.applied_q_target[11], in.q_target[11]);
  EXPECT_EQ(result.intervention_mask[11], lbeg::kNone);
}
