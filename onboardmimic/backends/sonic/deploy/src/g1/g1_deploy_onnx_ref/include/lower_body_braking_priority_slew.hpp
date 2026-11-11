#include "onboard_bit_cast.hpp"
/**
 * @file lower_body_braking_priority_slew.hpp
 * @brief Event-gated, braking-priority target innovation guard for G1
 * knee/ankle joints.
 *
 * A calibrated uncommanded impact opens a bounded recovery window.  During
 * that window only abrupt position-target changes that make an already
 * energy-adding virtual PD command more energy-adding are rate limited.
 * Changes that reduce positive power or strengthen braking pass immediately,
 * even when the resulting total command has not crossed zero power yet.  The
 * bound is deliberately relative to the previous *raw* policy target, not the
 * previously applied target: an intervention therefore cannot accumulate lag
 * and drag the gait phase away from the policy trajectory.
 */

#pragma once

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace lower_body_braking_priority_slew {

inline constexpr std::size_t kJointCount = 12;
inline constexpr std::array<std::size_t, 6> kMonitoredJointIndices = {
    3, 4, 5, 9, 10, 11};
inline constexpr double kDefaultMaxPositivePowerTargetStepRad = 0.13;
inline constexpr std::size_t kDefaultHoldTicks = 50;

struct Config {
  bool enabled = false;
  double max_positive_power_target_step_rad =
      kDefaultMaxPositivePowerTargetStepRad;
  std::size_t hold_ticks = kDefaultHoldTicks;
};

struct Result {
  bool valid = true;
  bool enabled = false;
  bool active = false;
  bool triggered = false;
  std::size_t hold_ticks_remaining = 0;
  std::array<double, kJointCount> raw_q_target{};
  std::array<double, kJointCount> applied_q_target{};
  std::array<double, kJointCount> raw_torque_nm{};
  std::array<double, kJointCount> raw_power_w{};
  std::array<std::uint8_t, kJointCount> intervention_mask{};
  std::size_t intervention_count = 0;
};

// Bit-level finite checks remain reliable with the deploy's -ffast-math.
inline bool IsFinite(double value) {
  constexpr std::uint64_t kExponentMask = UINT64_C(0x7ff0000000000000);
  return (onboard_bit_cast<std::uint64_t>(value) & kExponentMask) !=
         kExponentMask;
}

inline bool IsPositiveFinite(double value) {
  return IsFinite(value) && value > 0.0;
}

inline bool IsValidConfig(const Config& config) {
  return IsPositiveFinite(config.max_positive_power_target_step_rad) &&
         config.hold_ticks > 0;
}

class Limiter {
 public:
  Limiter() = default;

  explicit Limiter(const Config& config) { SetConfig(config); }

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

  void Reset() {
    previous_raw_q_target_.fill(0.0);
    has_previous_raw_q_target_ = false;
    hold_ticks_remaining_ = 0;
  }

  Result Apply(
      const std::array<double, kJointCount>& raw_q_target,
      const std::array<double, kJointCount>& measured_q,
      const std::array<double, kJointCount>& measured_dq,
      const std::array<double, kJointCount>& kp,
      const std::array<double, kJointCount>& kd,
      const std::array<double, kJointCount>& dq_target,
      const std::array<double, kJointCount>& tau_ff,
      bool external_impact_trigger,
      bool enforce = true) {
    Result result;
    result.enabled = config_.enabled;
    result.raw_q_target = raw_q_target;
    result.applied_q_target = raw_q_target;

    if (!config_valid_ || !AllFinite(raw_q_target) ||
        !AllFinite(measured_q) || !AllFinite(measured_dq) ||
        !AllFinite(kp) || !AllFinite(kd) || !AllFinite(dq_target) ||
        !AllFinite(tau_ff)) {
      result.valid = false;
      return result;
    }
    for (const double gain : kp) {
      if (gain <= 0.0) {
        result.valid = false;
        return result;
      }
    }

    if (!enforce || !config_.enabled) {
      hold_ticks_remaining_ = 0;
    } else if (external_impact_trigger && hold_ticks_remaining_ == 0) {
      hold_ticks_remaining_ = config_.hold_ticks;
      result.triggered = true;
    }
    result.active = config_.enabled && enforce && hold_ticks_remaining_ > 0;
    result.hold_ticks_remaining = hold_ticks_remaining_;

    for (std::size_t joint = 0; joint < kJointCount; ++joint) {
      const double raw_torque =
          kp[joint] * (raw_q_target[joint] - measured_q[joint]) +
          kd[joint] * (dq_target[joint] - measured_dq[joint]) +
          tau_ff[joint];
      const double raw_power = raw_torque * measured_dq[joint];
      if (!IsFinite(raw_torque) || !IsFinite(raw_power)) {
        result.valid = false;
        return result;
      }
      result.raw_torque_nm[joint] = raw_torque;
      result.raw_power_w[joint] = raw_power;
    }

    if (result.active && has_previous_raw_q_target_) {
      for (const std::size_t joint : kMonitoredJointIndices) {
        const double delta =
            raw_q_target[joint] - previous_raw_q_target_[joint];
        // The target change contributes kp * delta torque.  Classifying only
        // by the final total power is insufficient: a large target reduction
        // can still leave positive total power, and slewing that reduction
        // would delay braking.  Intervene only when both the final command and
        // this incremental target change add energy in the measured direction.
        const double incremental_target_power =
            kp[joint] * delta * measured_dq[joint];
        if (result.raw_power_w[joint] <= 0.0 ||
            incremental_target_power <= 0.0 ||
            std::abs(delta) <=
                config_.max_positive_power_target_step_rad) {
          continue;
        }
        result.applied_q_target[joint] =
            previous_raw_q_target_[joint] +
            std::copysign(
                config_.max_positive_power_target_step_rad, delta);
        result.intervention_mask[joint] = 1;
        ++result.intervention_count;
      }
    }

    // Never feed an intervention back into the next bound.  Recursive slew
    // accumulated target lag in closed loop and changed the landing phase;
    // this guard only removes the current frame's energy-adding innovation.
    previous_raw_q_target_ = raw_q_target;
    has_previous_raw_q_target_ = true;
    if (result.active) {
      --hold_ticks_remaining_;
    }
    return result;
  }

 private:
  static bool AllFinite(
      const std::array<double, kJointCount>& values) {
    return std::all_of(values.begin(), values.end(), IsFinite);
  }

  Config config_{};
  bool config_valid_ = true;
  std::array<double, kJointCount> previous_raw_q_target_{};
  bool has_previous_raw_q_target_ = false;
  std::size_t hold_ticks_remaining_ = 0;
};

}  // namespace lower_body_braking_priority_slew
