#include <gtest/gtest.h>

#include <limits>

#include "../include/lower_body_impact_damper.hpp"

namespace lbid = lower_body_impact_damper;

namespace {

std::array<double, lbid::kJointCount> BaseKd() {
  std::array<double, lbid::kJointCount> kd{};
  kd.fill(2.0);
  return kd;
}

}  // namespace

TEST(LowerBodyImpactDamper, DisabledModeIsExactPassThrough) {
  lbid::Damper damper;
  const auto raw = BaseKd();
  std::array<std::uint8_t, lbid::kJointCount> event{};
  event[11] = 1;
  const auto result = damper.Apply(raw, event);
  EXPECT_TRUE(result.valid);
  EXPECT_FALSE(result.enabled);
  EXPECT_FALSE(result.active);
  EXPECT_FALSE(result.triggered);
  EXPECT_EQ(result.applied_kd, raw);
}

TEST(LowerBodyImpactDamper, AppliesOnlyToTriggeredMonitoredJointAndDecays) {
  lbid::Config config;
  config.enabled = true;
  config.extra_kd_nm_per_rad_s = 3.0;
  config.hold_ticks = 3;
  lbid::Damper damper(config);
  const auto raw = BaseKd();
  std::array<std::uint8_t, lbid::kJointCount> event{};
  event[11] = 1;

  const auto first = damper.Apply(raw, event);
  ASSERT_TRUE(first.valid);
  EXPECT_TRUE(first.triggered);
  EXPECT_TRUE(first.active);
  EXPECT_EQ(first.trigger_mask[11], 1U);
  EXPECT_EQ(first.active_mask[11], 1U);
  EXPECT_DOUBLE_EQ(first.applied_extra_kd[11], 3.0);
  EXPECT_DOUBLE_EQ(first.applied_kd[11], 5.0);
  EXPECT_DOUBLE_EQ(first.applied_kd[10], 2.0);

  event.fill(0);
  const auto second = damper.Apply(raw, event);
  EXPECT_FALSE(second.triggered);
  EXPECT_DOUBLE_EQ(second.applied_extra_kd[11], 2.0);
  const auto third = damper.Apply(raw, event);
  EXPECT_DOUBLE_EQ(third.applied_extra_kd[11], 1.0);
  const auto fourth = damper.Apply(raw, event);
  EXPECT_FALSE(fourth.active);
  EXPECT_EQ(fourth.applied_kd, raw);
}

TEST(LowerBodyImpactDamper, CoupledModeDampsAllSixJointsAfterOneEvent) {
  lbid::Config config;
  config.enabled = true;
  config.extra_kd_nm_per_rad_s = 1.5;
  config.hold_ticks = 2;
  config.damp_all_monitored_joints = true;
  lbid::Damper damper(config);
  const auto raw = BaseKd();
  std::array<std::uint8_t, lbid::kJointCount> event{};
  event[11] = 1;

  const auto first = damper.Apply(raw, event);
  ASSERT_TRUE(first.valid);
  EXPECT_TRUE(first.triggered);
  EXPECT_EQ(first.active_count, lbid::kMonitoredJointIndices.size());
  for (const std::size_t joint : lbid::kMonitoredJointIndices) {
    EXPECT_EQ(first.trigger_mask[joint], 1U);
    EXPECT_EQ(first.active_mask[joint], 1U);
    EXPECT_DOUBLE_EQ(first.applied_extra_kd[joint], 1.5);
    EXPECT_DOUBLE_EQ(first.applied_kd[joint], 3.5);
  }
  EXPECT_DOUBLE_EQ(first.applied_kd[0], 2.0);

  event.fill(0);
  const auto second = damper.Apply(raw, event);
  EXPECT_FALSE(second.triggered);
  EXPECT_EQ(second.active_count, lbid::kMonitoredJointIndices.size());
  for (const std::size_t joint : lbid::kMonitoredJointIndices) {
    EXPECT_DOUBLE_EQ(second.applied_extra_kd[joint], 0.75);
  }
}

TEST(LowerBodyImpactDamper, IgnoresEventsOutsideKneesAndAnkles) {
  lbid::Config config;
  config.enabled = true;
  lbid::Damper damper(config);
  const auto raw = BaseKd();
  std::array<std::uint8_t, lbid::kJointCount> event{};
  event[0] = 1;
  const auto result = damper.Apply(raw, event);
  EXPECT_FALSE(result.triggered);
  EXPECT_FALSE(result.active);
  EXPECT_EQ(result.applied_kd, raw);
}

TEST(LowerBodyImpactDamper, RetriggerRestartsTheJointWindow) {
  lbid::Config config;
  config.enabled = true;
  config.extra_kd_nm_per_rad_s = 4.0;
  config.hold_ticks = 4;
  lbid::Damper damper(config);
  const auto raw = BaseKd();
  std::array<std::uint8_t, lbid::kJointCount> event{};
  event[4] = 1;
  damper.Apply(raw, event);
  event.fill(0);
  const auto decayed = damper.Apply(raw, event);
  EXPECT_DOUBLE_EQ(decayed.applied_extra_kd[4], 3.0);
  event[4] = 1;
  const auto restarted = damper.Apply(raw, event);
  EXPECT_TRUE(restarted.triggered);
  EXPECT_DOUBLE_EQ(restarted.applied_extra_kd[4], 4.0);
  EXPECT_EQ(restarted.hold_ticks_remaining[4], 4U);
}

TEST(LowerBodyImpactDamper, EnforceFalseClearsPendingRecovery) {
  lbid::Config config;
  config.enabled = true;
  lbid::Damper damper(config);
  const auto raw = BaseKd();
  std::array<std::uint8_t, lbid::kJointCount> event{};
  event[3] = 1;
  EXPECT_TRUE(damper.Apply(raw, event).active);
  event.fill(0);
  EXPECT_FALSE(damper.Apply(raw, event, false).active);
  EXPECT_FALSE(damper.Apply(raw, event).active);
}

TEST(LowerBodyImpactDamper, RejectsInvalidConfigAndNonfiniteInput) {
  lbid::Config config;
  config.enabled = true;
  config.extra_kd_nm_per_rad_s = 0.0;
  lbid::Damper damper;
  EXPECT_FALSE(damper.SetConfig(config));
  auto raw = BaseKd();
  std::array<std::uint8_t, lbid::kJointCount> event{};
  EXPECT_FALSE(damper.Apply(raw, event).valid);

  config.extra_kd_nm_per_rad_s = 3.0;
  ASSERT_TRUE(damper.SetConfig(config));
  raw[3] = std::numeric_limits<double>::quiet_NaN();
  EXPECT_FALSE(damper.Apply(raw, event).valid);
}
