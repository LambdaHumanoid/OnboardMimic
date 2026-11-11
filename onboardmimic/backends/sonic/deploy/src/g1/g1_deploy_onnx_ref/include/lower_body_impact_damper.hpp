#include "onboard_bit_cast.hpp"
/**
 * @file lower_body_impact_damper.hpp
 * @brief Local, transient damping after a calibrated uncommanded leg impact.
 *
 * The policy target remains live on every control tick.  Only Kd on the
 * detected knee/ankle is increased for a short, linearly decaying interval.
 * An optional coupled mode applies the same recovery to all six knees/ankles
 * after any calibrated event.  The added torque is always
 * -extra_kd * measured_dq and therefore removes mechanical energy instead of
 * freezing the leg or clipping its trajectory.
 */

#pragma once

#include <algorithm>
#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>

namespace lower_body_impact_damper {

inline constexpr std::size_t kJointCount = 12;
inline constexpr double kDefaultExtraKdNmPerRadS = 3.0;
inline constexpr std::size_t kDefaultHoldTicks = 5;
inline constexpr std::array<std::size_t, 6> kMonitoredJointIndices = {
    3, 4, 5, 9, 10, 11};

struct Config {
  bool enabled = false;
  double extra_kd_nm_per_rad_s = kDefaultExtraKdNmPerRadS;
  std::size_t hold_ticks = kDefaultHoldTicks;
  bool damp_all_monitored_joints = false;
};

struct Result {
  bool valid = true;
  bool enabled = false;
  bool active = false;
  bool triggered = false;
  std::array<double, kJointCount> raw_kd{};
  std::array<double, kJointCount> applied_kd{};
  std::array<double, kJointCount> applied_extra_kd{};
  std::array<std::uint8_t, kJointCount> trigger_mask{};
  std::array<std::uint8_t, kJointCount> active_mask{};
  std::array<std::size_t, kJointCount> hold_ticks_remaining{};
  std::size_t active_count = 0;
};

inline bool IsFinite(double value) {
  constexpr std::uint64_t kExponentMask = UINT64_C(0x7ff0000000000000);
  return (onboard_bit_cast<std::uint64_t>(value) & kExponentMask) !=
         kExponentMask;
}

inline bool IsPositiveFinite(double value) {
  return IsFinite(value) && value > 0.0;
}

inline bool IsValidConfig(const Config& config) {
  return IsPositiveFinite(config.extra_kd_nm_per_rad_s) &&
         config.hold_ticks > 0;
}

class Damper {
 public:
  Damper() = default;

  explicit Damper(const Config& config) { SetConfig(config); }

  bool SetConfig(const Config& config) {
    if (!IsValidConfig(config)) {
      config_valid_ = false;
      return false;
    }
    config_ = config;
    config_valid_ = true;
    Reset();
    return true;
  }

  const Config& config() const { return config_; }

  void Reset() { hold_ticks_remaining_.fill(0); }

  Result Apply(
      const std::array<double, kJointCount>& raw_kd,
      const std::array<std::uint8_t, kJointCount>& event_mask,
      bool enforce = true) {
    Result result;
    result.enabled = config_.enabled;
    result.raw_kd = raw_kd;
    result.applied_kd = raw_kd;

    if (!config_valid_ ||
        !std::all_of(raw_kd.begin(), raw_kd.end(), IsFinite) ||
        std::any_of(raw_kd.begin(), raw_kd.end(),
                    [](double value) { return value < 0.0; })) {
      result.valid = false;
      return result;
    }

    if (!config_.enabled || !enforce) {
      if (!enforce) {
        Reset();
      }
      return result;
    }

    const bool any_monitored_event = std::any_of(
        kMonitoredJointIndices.begin(), kMonitoredJointIndices.end(),
        [&event_mask](std::size_t joint) { return event_mask[joint] != 0; });
    for (const std::size_t joint : kMonitoredJointIndices) {
      const bool should_trigger = config_.damp_all_monitored_joints
                                      ? any_monitored_event
                                      : event_mask[joint] != 0;
      if (!should_trigger) {
        continue;
      }
      hold_ticks_remaining_[joint] = config_.hold_ticks;
      result.trigger_mask[joint] = 1;
      result.triggered = true;
    }

    for (const std::size_t joint : kMonitoredJointIndices) {
      const std::size_t remaining = hold_ticks_remaining_[joint];
      if (remaining == 0) {
        continue;
      }
      const double weight = static_cast<double>(remaining) /
                            static_cast<double>(config_.hold_ticks);
      const double extra_kd = config_.extra_kd_nm_per_rad_s * weight;
      result.applied_extra_kd[joint] = extra_kd;
      result.applied_kd[joint] += extra_kd;
      result.active_mask[joint] = 1;
      result.hold_ticks_remaining[joint] = remaining;
      ++result.active_count;
      --hold_ticks_remaining_[joint];
    }
    result.active = result.active_count != 0;
    return result;
  }

 private:
  Config config_{};
  bool config_valid_ = true;
  std::array<std::size_t, kJointCount> hold_ticks_remaining_{};
};

}  // namespace lower_body_impact_damper
