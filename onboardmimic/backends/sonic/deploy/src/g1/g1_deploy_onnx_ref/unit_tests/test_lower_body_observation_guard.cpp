#include <gtest/gtest.h>

#include <limits>

#include "../include/lower_body_observation_guard.hpp"

namespace lbog = lower_body_observation_guard;

namespace {

struct Inputs {
  std::array<double, lbog::kJointCount> q{};
  std::array<double, lbog::kJointCount> dq{};
  std::array<double, lbog::kJointCount> target{};
  std::array<double, lbog::kJointCount> kp{};
  std::array<double, lbog::kJointCount> kd{};

  Inputs() {
    kp.fill(100.0);
    kd.fill(2.0);
  }
};

}  // namespace

TEST(LowerBodyObservationGuard, RepairsCalibratedUncommandedSpike) {
  lbog::Config config;
  config.enabled = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t joint = 18;  // right ankle roll, policy order
  in.kp[joint] = 28.5;
  in.kd[joint] = 1.8;

  in.dq[joint] = -0.063;
  const auto first = guard.Update(100, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(first.triggered);

  in.dq[joint] = 3.310;
  in.target[joint] = 0.019;
  const auto shock = guard.Update(101, in.q, in.dq, in.target, in.kp, in.kd);
  ASSERT_TRUE(shock.valid);
  ASSERT_TRUE(shock.triggered);
  EXPECT_EQ(shock.repair_count, 1U);
  EXPECT_EQ(shock.repair_mask[joint], 1U);
  EXPECT_DOUBLE_EQ(shock.policy_dq[joint], -0.063);

  std::array<double, lbog::kJointCount> historical{};
  ASSERT_TRUE(guard.Lookup(101, historical));
  EXPECT_DOUBLE_EQ(historical[joint], -0.063);
}

TEST(LowerBodyObservationGuard, DoesNotRepairCommandedMotion) {
  lbog::Config config;
  config.enabled = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t joint = 18;
  guard.Update(1, in.q, in.dq, in.target, in.kp, in.kd);

  in.dq[joint] = 4.0;
  in.target[joint] = 0.10;
  const auto commanded =
      guard.Update(2, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(commanded.triggered);
  EXPECT_DOUBLE_EQ(commanded.policy_dq[joint], 4.0);
}

TEST(LowerBodyObservationGuard, DoesNotRepairHighPriorTorqueResponse) {
  lbog::Config config;
  config.enabled = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t joint = 18;
  guard.Update(1, in.q, in.dq, in.target, in.kp, in.kd);

  in.dq[joint] = 4.0;
  in.target[joint] = 0.03;  // target delta passes, 3 Nm torque does not
  const auto driven = guard.Update(2, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(driven.triggered);
  EXPECT_DOUBLE_EQ(driven.policy_dq[joint], 4.0);
}

TEST(LowerBodyObservationGuard, DisabledModeIsExactPassThrough) {
  lbog::Guard guard;
  Inputs in;
  constexpr std::size_t joint = 9;
  in.dq[joint] = -1.0;
  guard.Update(7, in.q, in.dq, in.target, in.kp, in.kd);
  in.dq[joint] = 7.0;
  const auto result = guard.Update(8, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(result.enabled);
  EXPECT_FALSE(result.triggered);
  EXPECT_EQ(result.policy_dq, in.dq);
}

TEST(LowerBodyObservationGuard, EnforceFalseWarmsHistoryWithoutRepair) {
  lbog::Config config;
  config.enabled = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t joint = 13;
  guard.Update(20, in.q, in.dq, in.target, in.kp, in.kd, false);
  in.dq[joint] = 4.0;
  const auto warmup =
      guard.Update(21, in.q, in.dq, in.target, in.kp, in.kd, false);
  EXPECT_TRUE(warmup.enabled);
  EXPECT_FALSE(warmup.active);
  EXPECT_FALSE(warmup.triggered);
  EXPECT_DOUBLE_EQ(warmup.policy_dq[joint], 4.0);
}

TEST(LowerBodyObservationGuard, DetectOnlyLeavesObservationUntouched) {
  lbog::Config config;
  config.enabled = true;
  config.repair_observation = false;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t joint = 18;
  in.kp[joint] = 28.5;
  in.kd[joint] = 1.8;
  in.dq[joint] = -0.063;
  guard.Update(30, in.q, in.dq, in.target, in.kp, in.kd);
  in.dq[joint] = 3.310;
  in.target[joint] = 0.019;
  const auto result =
      guard.Update(31, in.q, in.dq, in.target, in.kp, in.kd);
  ASSERT_TRUE(result.triggered);
  EXPECT_FALSE(result.repair_enabled);
  EXPECT_EQ(result.event_count, 1U);
  EXPECT_EQ(result.event_mask[joint], 1U);
  EXPECT_EQ(result.repair_count, 0U);
  EXPECT_EQ(result.repair_mask[joint], 0U);
  EXPECT_DOUBLE_EQ(result.policy_dq[joint], 3.310);
}

TEST(LowerBodyObservationGuard, SustainedFilterCoversAllMonitoredJoints) {
  lbog::Config config;
  config.enabled = true;
  config.repair_hold_ticks = 3;
  config.repair_low_pass_alpha = 0.5;
  config.repair_all_monitored_joints = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t trigger_joint = 18;
  constexpr std::size_t coupled_joint = 9;
  in.kp[trigger_joint] = 28.5;
  in.kd[trigger_joint] = 1.8;
  in.dq[trigger_joint] = -0.1;
  in.dq[coupled_joint] = 1.0;
  guard.Update(100, in.q, in.dq, in.target, in.kp, in.kd);

  in.dq[trigger_joint] = 3.3;
  in.dq[coupled_joint] = 3.0;
  const auto first =
      guard.Update(101, in.q, in.dq, in.target, in.kp, in.kd);
  ASSERT_TRUE(first.triggered);
  EXPECT_EQ(first.repair_count, lbog::kMonitoredJointIndices.size());
  EXPECT_DOUBLE_EQ(first.policy_dq[trigger_joint], 1.6);
  EXPECT_DOUBLE_EQ(first.policy_dq[coupled_joint], 2.0);

  // Avoid retriggering while demonstrating the bounded three-tick window.
  in.target[trigger_joint] = 0.1;
  in.dq[trigger_joint] = 3.3;
  in.dq[coupled_joint] = 3.0;
  const auto second =
      guard.Update(102, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(second.triggered);
  EXPECT_EQ(second.repair_count, lbog::kMonitoredJointIndices.size());
  EXPECT_DOUBLE_EQ(second.policy_dq[trigger_joint], 2.45);
  EXPECT_DOUBLE_EQ(second.policy_dq[coupled_joint], 2.5);

  const auto third =
      guard.Update(103, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_EQ(third.repair_count, lbog::kMonitoredJointIndices.size());
  EXPECT_DOUBLE_EQ(third.policy_dq[trigger_joint], 2.875);
  EXPECT_DOUBLE_EQ(third.policy_dq[coupled_joint], 2.75);

  const auto after =
      guard.Update(104, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_EQ(after.repair_count, 0U);
  EXPECT_DOUBLE_EQ(after.policy_dq[trigger_joint], 3.3);
  EXPECT_DOUBLE_EQ(after.policy_dq[coupled_joint], 3.0);
}

TEST(LowerBodyObservationGuard, NonControlStateClearsSustainedFilter) {
  lbog::Config config;
  config.enabled = true;
  config.repair_hold_ticks = 10;
  config.repair_low_pass_alpha = 0.25;
  config.repair_all_monitored_joints = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t joint = 18;
  in.kp[joint] = 28.5;
  in.kd[joint] = 1.8;
  guard.Update(1, in.q, in.dq, in.target, in.kp, in.kd);
  in.dq[joint] = 4.0;
  ASSERT_TRUE(guard.Update(2, in.q, in.dq, in.target, in.kp, in.kd).triggered);

  const auto paused =
      guard.Update(3, in.q, in.dq, in.target, in.kp, in.kd, false);
  EXPECT_FALSE(paused.active);
  EXPECT_EQ(paused.repair_count, 0U);
  EXPECT_EQ(paused.policy_dq, in.dq);

  const auto resumed =
      guard.Update(4, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_EQ(resumed.repair_count, 0U);
  EXPECT_EQ(resumed.policy_dq, in.dq);
}

TEST(LowerBodyObservationGuard, ClipsPolicyDqInnovationDuringRepairWindow) {
  lbog::Config config;
  config.enabled = true;
  config.repair_hold_ticks = 3;
  config.repair_low_pass_alpha = 0.0;
  config.repair_max_policy_dq_step_rad_s = 3.25;
  config.repair_all_monitored_joints = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t trigger_joint = 18;
  constexpr std::size_t coupled_joint = 9;
  constexpr std::size_t unmonitored_joint = 0;
  in.kp[trigger_joint] = 28.5;
  in.kd[trigger_joint] = 1.8;
  guard.Update(200, in.q, in.dq, in.target, in.kp, in.kd);

  in.dq[trigger_joint] = 4.0;
  in.dq[coupled_joint] = -4.0;
  in.dq[unmonitored_joint] = 9.0;
  const auto first =
      guard.Update(201, in.q, in.dq, in.target, in.kp, in.kd);
  ASSERT_TRUE(first.triggered);
  EXPECT_DOUBLE_EQ(first.policy_dq[trigger_joint], 3.25);
  EXPECT_DOUBLE_EQ(first.policy_dq[coupled_joint], -3.25);
  EXPECT_DOUBLE_EQ(first.policy_dq[unmonitored_joint], 9.0);

  // Keep the detector from retriggering while proving the recursive bound.
  in.target[trigger_joint] = 0.1;
  in.dq[trigger_joint] = 8.0;
  in.dq[coupled_joint] = -8.0;
  const auto second =
      guard.Update(202, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(second.triggered);
  EXPECT_DOUBLE_EQ(second.policy_dq[trigger_joint], 6.5);
  EXPECT_DOUBLE_EQ(second.policy_dq[coupled_joint], -6.5);
}

TEST(LowerBodyObservationGuard, FixedRepairWindowDoesNotExtendOnRetrigger) {
  lbog::Config config;
  config.enabled = true;
  config.repair_hold_ticks = 3;
  config.repair_max_policy_dq_step_rad_s = 3.25;
  config.repair_all_monitored_joints = true;
  config.extend_repair_window_on_retrigger = false;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t trigger_joint = 18;
  in.kd[trigger_joint] = 0.0;
  guard.Update(400, in.q, in.dq, in.target, in.kp, in.kd);

  in.dq[trigger_joint] = 4.0;
  const auto first =
      guard.Update(401, in.q, in.dq, in.target, in.kp, in.kd);
  ASSERT_TRUE(first.triggered);
  EXPECT_EQ(first.repair_count, lbog::kMonitoredJointIndices.size());

  in.dq[trigger_joint] = 8.0;
  const auto retrigger =
      guard.Update(402, in.q, in.dq, in.target, in.kp, in.kd);
  ASSERT_TRUE(retrigger.triggered);
  EXPECT_EQ(retrigger.repair_count, lbog::kMonitoredJointIndices.size());

  const auto last =
      guard.Update(403, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_EQ(last.repair_count, lbog::kMonitoredJointIndices.size());

  const auto after =
      guard.Update(404, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_EQ(after.repair_count, 0U);
  EXPECT_EQ(after.policy_dq, in.dq);
}

TEST(LowerBodyObservationGuard, RepairCanCoverOnlyBothAnkleRollJoints) {
  lbog::Config config;
  config.enabled = true;
  config.repair_both_ankle_roll_joints = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t trigger_joint = 18;
  constexpr std::size_t other_roll_joint = 17;
  constexpr std::size_t knee_joint = 9;
  in.kp[trigger_joint] = 28.5;
  in.kd[trigger_joint] = 1.8;
  guard.Update(500, in.q, in.dq, in.target, in.kp, in.kd);

  in.dq[trigger_joint] = 4.0;
  const auto result =
      guard.Update(501, in.q, in.dq, in.target, in.kp, in.kd);
  ASSERT_TRUE(result.triggered);
  EXPECT_EQ(result.repair_count, 2U);
  EXPECT_EQ(result.repair_mask[trigger_joint], 1U);
  EXPECT_EQ(result.repair_mask[other_roll_joint], 1U);
  EXPECT_EQ(result.repair_mask[knee_joint], 0U);
}

TEST(LowerBodyObservationGuard, ContinuousClipOnlyRepairsActualViolation) {
  lbog::Config config;
  config.enabled = true;
  config.repair_max_policy_dq_step_rad_s = 3.25;
  config.continuous_innovation_clip = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t clipped_joint = 18;
  constexpr std::size_t unchanged_joint = 17;
  guard.Update(600, in.q, in.dq, in.target, in.kp, in.kd);

  // A commanded target change suppresses the detector, but continuous mode
  // still applies the independently calibrated observation bound.
  in.dq[clipped_joint] = 4.0;
  in.target[clipped_joint] = 0.1;
  const auto clipped =
      guard.Update(601, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(clipped.triggered);
  EXPECT_EQ(clipped.repair_count, 1U);
  EXPECT_EQ(clipped.repair_mask[clipped_joint], 1U);
  EXPECT_EQ(clipped.repair_mask[unchanged_joint], 0U);
  EXPECT_DOUBLE_EQ(clipped.policy_dq[clipped_joint], 3.25);
  EXPECT_DOUBLE_EQ(clipped.policy_dq[unchanged_joint], 0.0);

  in.dq[clipped_joint] = 4.1;
  const auto caught_up =
      guard.Update(602, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_EQ(caught_up.repair_count, 0U);
  EXPECT_DOUBLE_EQ(caught_up.policy_dq[clipped_joint], 4.1);
}

TEST(LowerBodyObservationGuard, EventLocalClipEndsOnlyAfterCatchingUp) {
  lbog::Config config;
  config.enabled = true;
  config.repair_max_policy_dq_step_rad_s = 3.25;
  config.repair_until_caught_up = true;
  config.trigger_ankle_roll_only = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t joint = 18;
  in.kp[joint] = 28.5;
  in.kd[joint] = 1.8;
  guard.Update(700, in.q, in.dq, in.target, in.kp, in.kd);

  in.dq[joint] = 8.0;
  const auto triggered =
      guard.Update(701, in.q, in.dq, in.target, in.kp, in.kd);
  ASSERT_TRUE(triggered.triggered);
  EXPECT_EQ(triggered.repair_count, 1U);
  EXPECT_DOUBLE_EQ(triggered.policy_dq[joint], 3.25);

  const auto following =
      guard.Update(702, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(following.triggered);
  EXPECT_EQ(following.repair_count, 1U);
  EXPECT_DOUBLE_EQ(following.policy_dq[joint], 6.5);

  const auto caught_up =
      guard.Update(703, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_EQ(caught_up.repair_count, 0U);
  EXPECT_DOUBLE_EQ(caught_up.policy_dq[joint], 8.0);

  // Once caught up, a large but explicitly commanded change is untouched.
  in.dq[joint] = 3.0;
  in.target[joint] = 0.1;
  const auto commanded =
      guard.Update(704, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(commanded.triggered);
  EXPECT_EQ(commanded.repair_count, 0U);
  EXPECT_DOUBLE_EQ(commanded.policy_dq[joint], 3.0);
}

TEST(LowerBodyObservationGuard,
     PositionConsistencyRepairPreservesPhysicalMotionAndBoundsResidual) {
  lbog::Config config;
  config.enabled = true;
  config.repair_hold_ticks = 2;
  config.repair_max_position_dq_residual_rad_s = 1.0;
  config.control_dt_s = 0.02;
  config.repair_all_monitored_joints = true;
  config.trigger_ankle_roll_only = true;
  config.extend_repair_window_on_retrigger = false;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t trigger_joint = 18;
  constexpr std::size_t coupled_joint = 9;
  constexpr std::size_t unmonitored_joint = 0;
  in.kp[trigger_joint] = 28.5;
  in.kd[trigger_joint] = 1.8;
  guard.Update(800, in.q, in.dq, in.target, in.kp, in.kd);

  // q supports +1 rad/s at the ankle roll, while raw dq reports +4.5.  The
  // repair preserves that physical +1 and admits at most +1 residual.
  in.q[trigger_joint] = 0.02;
  in.dq[trigger_joint] = 4.5;
  // A simultaneous sub-threshold coupled motion is repaired because the real
  // capture showed correlated lower-leg observations after ankle-roll onset.
  in.q[coupled_joint] = 0.01;
  in.dq[coupled_joint] = 2.5;
  in.dq[unmonitored_joint] = 9.0;
  const auto first =
      guard.Update(801, in.q, in.dq, in.target, in.kp, in.kd);
  ASSERT_TRUE(first.triggered);
  EXPECT_EQ(first.repair_count, lbog::kMonitoredJointIndices.size());
  EXPECT_DOUBLE_EQ(first.policy_dq[trigger_joint], 2.0);
  EXPECT_DOUBLE_EQ(first.policy_dq[coupled_joint], 1.5);
  EXPECT_DOUBLE_EQ(first.policy_dq[unmonitored_joint], 9.0);

  // The second bounded tick is anchored to the new position interval rather
  // than recursively catching up to the prior repaired value.
  in.q[trigger_joint] = 0.03;  // +0.5 rad/s from q
  in.dq[trigger_joint] = 3.0;
  in.q[coupled_joint] = 0.006;  // -0.2 rad/s from q
  in.dq[coupled_joint] = -2.0;
  in.target[trigger_joint] = 0.1;  // suppress retrigger
  const auto second =
      guard.Update(802, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(second.triggered);
  EXPECT_DOUBLE_EQ(second.policy_dq[trigger_joint], 1.5);
  EXPECT_DOUBLE_EQ(second.policy_dq[coupled_joint], -1.2);

  const auto after =
      guard.Update(803, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_EQ(after.repair_count, 0U);
  EXPECT_EQ(after.policy_dq, in.dq);
}

TEST(LowerBodyObservationGuard,
     PositionConsistencyModeLeavesNonEventFramesExact) {
  lbog::Config config;
  config.enabled = true;
  config.repair_max_position_dq_residual_rad_s = 1.0;
  config.trigger_ankle_roll_only = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t joint = 18;
  guard.Update(900, in.q, in.dq, in.target, in.kp, in.kd);

  in.q[joint] = 0.02;
  in.dq[joint] = 2.0;  // below the calibrated event threshold
  const auto smooth =
      guard.Update(901, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(smooth.triggered);
  EXPECT_EQ(smooth.repair_count, 0U);
  EXPECT_EQ(smooth.policy_dq, in.dq);
}

TEST(LowerBodyObservationGuard, AnkleRollOnlyTriggerRejectsPitchImpact) {
  lbog::Config config;
  config.enabled = true;
  config.trigger_ankle_roll_only = true;
  lbog::Guard guard(config);
  Inputs in;
  constexpr std::size_t ankle_pitch_joint = 13;
  constexpr std::size_t ankle_roll_joint = 17;
  in.kp[ankle_pitch_joint] = 28.5;
  in.kd[ankle_pitch_joint] = 1.8;
  in.kp[ankle_roll_joint] = 28.5;
  in.kd[ankle_roll_joint] = 1.8;
  guard.Update(300, in.q, in.dq, in.target, in.kp, in.kd);

  in.dq[ankle_pitch_joint] = 4.0;
  const auto pitch =
      guard.Update(301, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_FALSE(pitch.triggered);
  EXPECT_EQ(pitch.repair_count, 0U);
  EXPECT_DOUBLE_EQ(pitch.policy_dq[ankle_pitch_joint], 4.0);

  in.dq[ankle_roll_joint] = 4.0;
  const auto roll =
      guard.Update(302, in.q, in.dq, in.target, in.kp, in.kd);
  EXPECT_TRUE(roll.triggered);
  EXPECT_EQ(roll.event_mask[ankle_roll_joint], 1U);
}

TEST(LowerBodyObservationGuard, RejectsInvalidSustainedFilterConfig) {
  lbog::Guard guard;
  lbog::Config config;
  config.repair_hold_ticks = 0;
  EXPECT_FALSE(guard.SetConfig(config));
  config.repair_hold_ticks = 1;
  config.repair_low_pass_alpha = 1.01;
  EXPECT_FALSE(guard.SetConfig(config));
  config.repair_low_pass_alpha = -0.01;
  EXPECT_FALSE(guard.SetConfig(config));
  config.repair_low_pass_alpha = 0.0;
  config.repair_max_policy_dq_step_rad_s = -0.01;
  EXPECT_FALSE(guard.SetConfig(config));
  config.repair_max_policy_dq_step_rad_s =
      std::numeric_limits<double>::quiet_NaN();
  EXPECT_FALSE(guard.SetConfig(config));
  config.repair_max_policy_dq_step_rad_s = 0.0;
  config.continuous_innovation_clip = true;
  EXPECT_FALSE(guard.SetConfig(config));
  config.continuous_innovation_clip = false;
  config.repair_until_caught_up = true;
  EXPECT_FALSE(guard.SetConfig(config));
  config.repair_max_policy_dq_step_rad_s = 3.25;
  config.continuous_innovation_clip = true;
  EXPECT_FALSE(guard.SetConfig(config));
  config.continuous_innovation_clip = false;
  config.repair_until_caught_up = false;
  config.repair_max_policy_dq_step_rad_s = 0.0;
  config.repair_max_position_dq_residual_rad_s = -0.01;
  EXPECT_FALSE(guard.SetConfig(config));
  config.repair_max_position_dq_residual_rad_s =
      std::numeric_limits<double>::quiet_NaN();
  EXPECT_FALSE(guard.SetConfig(config));
  config.repair_max_position_dq_residual_rad_s = 1.0;
  config.control_dt_s = 0.0;
  EXPECT_FALSE(guard.SetConfig(config));
  config.control_dt_s = 0.02;
  config.repair_max_policy_dq_step_rad_s = 3.25;
  EXPECT_FALSE(guard.SetConfig(config));
  config.repair_max_policy_dq_step_rad_s = 0.0;
  config.continuous_innovation_clip = true;
  EXPECT_FALSE(guard.SetConfig(config));
  config.continuous_innovation_clip = false;
  config.repair_until_caught_up = true;
  EXPECT_FALSE(guard.SetConfig(config));
}
