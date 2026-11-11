#include "onboard_bit_cast.hpp"
#ifndef LEFT_SHOULDER_BACK_OFFSET_HPP
#define LEFT_SHOULDER_BACK_OFFSET_HPP

#include <algorithm>
#include <bit>
#include <cstdint>

namespace left_shoulder_back_offset {

inline constexpr int kMotorIndex = 15;  // LeftShoulderPitch.
inline constexpr double kJointMinRad = -3.0892;
inline constexpr double kJointMaxRad = 2.6704;
inline constexpr double kMaxOffsetDeg = 90.0;
inline constexpr double kPi = 3.14159265358979323846;

inline bool IsFinite(double value) {
  const std::uint64_t bits = onboard_bit_cast<std::uint64_t>(value);
  return (bits & UINT64_C(0x7ff0000000000000)) !=
         UINT64_C(0x7ff0000000000000);
}

inline bool IsValidOffsetDeg(double value) {
  return IsFinite(value) && value >= 0.0 && value <= kMaxOffsetDeg;
}

struct Result {
  bool valid = false;
  bool clamped = false;
  double target_rad = 0.0;
};

// Positive G1 left-shoulder pitch moves the elbow toward the robot's rear.
inline Result Apply(double original_target_rad, double offset_deg) {
  Result result;
  if (!IsFinite(original_target_rad) || !IsValidOffsetDeg(offset_deg)) {
    return result;
  }
  const double requested =
      original_target_rad + offset_deg * kPi / 180.0;
  result.target_rad =
      std::clamp(requested, kJointMinRad, kJointMaxRad);
  result.clamped = result.target_rad != requested;
  result.valid = true;
  return result;
}

}  // namespace left_shoulder_back_offset

#endif  // LEFT_SHOULDER_BACK_OFFSET_HPP
