#include <array>
#include <limits>

#include <gtest/gtest.h>

#include "../include/joint_velocity_safety.hpp"

namespace jvs = joint_velocity_safety;

TEST(JointVelocitySafety, OnlyLoopbackRobotEndpointsAreSimulation) {
  EXPECT_TRUE(jvs::IsSimulationRobotEndpoint("localhost"));
  EXPECT_TRUE(jvs::IsSimulationRobotEndpoint("127.0.0.1"));
  EXPECT_TRUE(jvs::IsSimulationRobotEndpoint("::1"));
  EXPECT_FALSE(jvs::IsSimulationRobotEndpoint("192.168.31.62"));
  EXPECT_FALSE(jvs::IsSimulationRobotEndpoint("unitree"));
  EXPECT_FALSE(jvs::IsSimulationRobotEndpoint(""));
}

TEST(JointVelocitySafety, MeasuredSpeedChecksBothDirections) {
  std::array<float, 29> measured{};
  measured[4] = -10.01F;

  const auto result = jvs::CheckMeasuredSpeeds(measured, 10.0);
  EXPECT_TRUE(result.valid);
  EXPECT_EQ(result.violation_count, 1U);
  EXPECT_TRUE(result.violating[4]);
  EXPECT_EQ(result.worst_index, 4U);
}

TEST(JointVelocitySafety, DefaultMeasuredSpeedLimitIsTwelvePointFiveRadPerSecond) {
  std::array<float, 29> measured{};
  measured[5] = 12.016F;

  const auto below_limit = jvs::CheckMeasuredSpeeds(measured);
  EXPECT_TRUE(below_limit.valid);
  EXPECT_EQ(below_limit.violation_count, 0U);

  measured[5] = 12.51F;
  const auto above_limit = jvs::CheckMeasuredSpeeds(measured);
  EXPECT_TRUE(above_limit.valid);
  EXPECT_EQ(above_limit.violation_count, 1U);
  EXPECT_TRUE(above_limit.violating[5]);
}

TEST(JointVelocitySafety, MeasuredSpeedRejectsNonFiniteState) {
  std::array<float, 29> measured{};
  measured[9] = std::numeric_limits<float>::infinity();

  const auto result = jvs::CheckMeasuredSpeeds(measured);
  EXPECT_FALSE(result.valid);
  EXPECT_EQ(result.worst_index, 9U);
}

TEST(JointVelocitySafety, MeasuredSpeedAtLimitIsAccepted) {
  std::array<float, 29> measured{};
  measured[3] = 10.0F;
  measured[8] = -10.0F;

  const auto result = jvs::CheckMeasuredSpeeds(measured, 10.0);
  EXPECT_TRUE(result.valid);
  EXPECT_EQ(result.violation_count, 0U);
}

TEST(JointVelocitySafety, MeasuredSpeedRejectsInvalidLimit) {
  std::array<float, 29> measured{};

  const auto zero_limit = jvs::CheckMeasuredSpeeds(measured, 0.0);
  EXPECT_FALSE(zero_limit.valid);
  const auto nan_limit = jvs::CheckMeasuredSpeeds(
      measured, std::numeric_limits<double>::quiet_NaN());
  EXPECT_FALSE(nan_limit.valid);
}
