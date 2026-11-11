#include "onboard_bit_cast.hpp"
#ifndef JOINT_VELOCITY_SAFETY_HPP
#define JOINT_VELOCITY_SAFETY_HPP

#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace joint_velocity_safety {

inline constexpr double kDefaultMeasuredSpeedLimitRadS = 12.5;
inline constexpr std::array<const char*, 29> kG1HardwareJointNames = {
    "left_hip_pitch",      "left_hip_roll",       "left_hip_yaw",
    "left_knee",           "left_ankle_pitch",    "left_ankle_roll",
    "right_hip_pitch",     "right_hip_roll",      "right_hip_yaw",
    "right_knee",          "right_ankle_pitch",   "right_ankle_roll",
    "waist_yaw",           "waist_roll",          "waist_pitch",
    "left_shoulder_pitch", "left_shoulder_roll",  "left_shoulder_yaw",
    "left_elbow",          "left_wrist_roll",     "left_wrist_pitch",
    "left_wrist_yaw",      "right_shoulder_pitch", "right_shoulder_roll",
    "right_shoulder_yaw",  "right_elbow",         "right_wrist_roll",
    "right_wrist_pitch",   "right_wrist_yaw",
};

inline bool IsFinite(double value) {
  const std::uint64_t bits = onboard_bit_cast<std::uint64_t>(value);
  return (bits & UINT64_C(0x7ff0000000000000)) !=
         UINT64_C(0x7ff0000000000000);
}

inline double Abs(double value) {
  return value < 0.0 ? -value : value;
}

inline bool IsPositiveFinite(double value) {
  return IsFinite(value) && value > 0.0;
}

inline bool IsSimulationRobotEndpoint(std::string_view endpoint) {
  return endpoint == "localhost" || endpoint == "127.0.0.1" ||
         endpoint == "::1";
}

template <std::size_t N>
struct SpeedCheck {
  bool valid = true;
  std::size_t violation_count = 0;
  std::size_t worst_index = 0;
  double worst_speed_rad_s = 0.0;
  std::array<bool, N> violating{};
  std::array<double, N> speed_rad_s{};
};

/**
 * @brief Check measured motor velocities in hardware order.
 *
 * Both directions are checked. A non-finite measurement is invalid rather
 * than merely over-limit so the caller can stop immediately.
 */
template <typename Value, std::size_t N>
SpeedCheck<N> CheckMeasuredSpeeds(
    const std::array<Value, N>& measured_dq,
    double limit_rad_s = kDefaultMeasuredSpeedLimitRadS) {
  // Gate physical feedback only. SONIC's adjacent PD position targets may
  // legitimately jump and are not a measured or requested joint velocity.
  SpeedCheck<N> result;
  if (!IsPositiveFinite(limit_rad_s)) {
    result.valid = false;
    return result;
  }

  for (std::size_t i = 0; i < N; ++i) {
    const double value = static_cast<double>(measured_dq[i]);
    if (!IsFinite(value)) {
      result.valid = false;
      result.worst_index = i;
      return result;
    }
    const double speed = Abs(value);
    result.speed_rad_s[i] = speed;
    if (speed > result.worst_speed_rad_s) {
      result.worst_speed_rad_s = speed;
      result.worst_index = i;
    }
    if (speed > limit_rad_s) {
      result.violating[i] = true;
      ++result.violation_count;
    }
  }
  return result;
}

}  // namespace joint_velocity_safety

#endif  // JOINT_VELOCITY_SAFETY_HPP
