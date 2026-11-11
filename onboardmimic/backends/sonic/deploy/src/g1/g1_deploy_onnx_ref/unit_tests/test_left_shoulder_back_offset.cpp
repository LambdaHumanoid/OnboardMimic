#include <limits>

#include <gtest/gtest.h>

#include "left_shoulder_back_offset.hpp"

TEST(LeftShoulderBackOffset, ZeroIsPassThrough) {
  const auto result = left_shoulder_back_offset::Apply(0.2, 0.0);
  ASSERT_TRUE(result.valid);
  EXPECT_FALSE(result.clamped);
  EXPECT_DOUBLE_EQ(result.target_rad, 0.2);
}

TEST(LeftShoulderBackOffset, ThirtyDegreesAddsPositivePitch) {
  const auto result = left_shoulder_back_offset::Apply(0.2, 30.0);
  ASSERT_TRUE(result.valid);
  EXPECT_FALSE(result.clamped);
  EXPECT_NEAR(
      result.target_rad,
      0.2 + left_shoulder_back_offset::kPi / 6.0,
      1e-12);
}

TEST(LeftShoulderBackOffset, RespectsUpperJointLimit) {
  const auto result = left_shoulder_back_offset::Apply(2.5, 30.0);
  ASSERT_TRUE(result.valid);
  EXPECT_TRUE(result.clamped);
  EXPECT_DOUBLE_EQ(
      result.target_rad, left_shoulder_back_offset::kJointMaxRad);
}

TEST(LeftShoulderBackOffset, RejectsUnsafeConfiguration) {
  EXPECT_FALSE(left_shoulder_back_offset::Apply(0.0, -1.0).valid);
  EXPECT_FALSE(left_shoulder_back_offset::Apply(0.0, 90.1).valid);
  EXPECT_FALSE(left_shoulder_back_offset::Apply(
      std::numeric_limits<double>::quiet_NaN(), 30.0).valid);
}
