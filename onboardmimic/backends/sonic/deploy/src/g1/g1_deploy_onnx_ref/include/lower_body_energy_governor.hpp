#include "onboard_bit_cast.hpp"
/**
 * @file lower_body_energy_governor.hpp
 * @brief Braking-priority positive-energy envelope for the 12 G1 leg joints.
 *
 * The policy still chooses every joint target.  This governor only reduces the
 * virtual PD torque when that torque would add mechanical energy outside the
 * envelope observed in successful MuJoCo replays.  Dissipative/braking torque
 * is never delayed by the power/work limits (the effort envelope still
 * applies in both directions).
 */

#pragma once

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace lower_body_energy_governor {

inline constexpr std::size_t kJointCount = 12;
inline constexpr std::size_t kWindowSamples = 5;
inline constexpr std::size_t kPreviousWorkSamples = kWindowSamples - 1;
inline constexpr double kDefaultControlDt = 0.02;
inline constexpr std::size_t kTremorHistorySamples = 3;
inline constexpr double kDefaultTremorTriggerThresholdRadS = 3.0;
inline constexpr double kDefaultTremorPeakToPeakThresholdRadS = 9.0;
inline constexpr std::size_t kDefaultTremorHoldTicks = 50;

// Knees and ankles are where the recorded real-robot event first appeared.
// Hip motion is deliberately excluded because normal turning produces large
// low-frequency hip-velocity changes that are not the observed tremor mode.
inline constexpr std::array<std::size_t, 6> kTremorJointIndices = {
    3, 4, 5, 9, 10, 11};

// MuJoCo/hardware order:
// L hip pitch/roll/yaw, knee, ankle pitch/roll, then the right side.
inline constexpr std::array<double, kJointCount> kEffortLimitsNm = {
    139.0, 139.0, 88.0, 139.0, 50.0, 50.0,
    139.0, 139.0, 88.0, 139.0, 50.0, 50.0};

// Pair-symmetric p99.9 successful-simulation envelope with 25% margin.
// Calibration source: successful v4 elastic-release replays, 2026-08-31.
inline constexpr std::array<double, kJointCount>
    kPositivePowerLimitsW = {
        35.548, 26.355, 9.975, 60.488, 42.674, 3.057,
        35.548, 26.355, 9.975, 60.488, 42.674, 3.057};

// Maximum positive work over five 50-Hz samples (100 ms total span).
inline constexpr std::array<double, kJointCount>
    kPositiveWorkLimitsJ = {
        3.1541, 2.4507, 0.8676, 4.9314, 3.5869, 0.2380,
        3.1541, 2.4507, 0.8676, 4.9314, 3.5869, 0.2380};

enum Intervention : std::uint8_t {
  kNone = 0,
  kEffort = 1U << 0,
  kPositivePower = 1U << 1,
  kPositiveWork = 1U << 2,
  kRecoveryDamping = 1U << 3,
};

struct Config {
  bool enabled = false;
  double dt = kDefaultControlDt;
  // Scales only the learned positive-power/work envelope.  It does not scale
  // the actuator effort limits.
  double envelope_scale = 1.0;
  // Optional event gate.  Two above-threshold, opposite-signed velocity
  // innovations on the same knee/ankle within three 50-Hz samples activate
  // the strong envelope for a bounded recovery interval.
  bool tremor_gate_enabled = false;
  double tremor_trigger_threshold_rad_s =
      kDefaultTremorTriggerThresholdRadS;
  double tremor_peak_to_peak_threshold_rad_s =
      kDefaultTremorPeakToPeakThresholdRadS;
  std::size_t tremor_hold_ticks = kDefaultTremorHoldTicks;
  // Optional event-recovery damping on the monitored knees/ankles.  The
  // added torque is always -gain*dq, so it can only remove mechanical energy.
  double tremor_recovery_damping_nm_per_rad_s = 0.0;
  std::array<double, kJointCount> effort_limits_nm = kEffortLimitsNm;
  std::array<double, kJointCount> positive_power_limits_w =
      kPositivePowerLimitsW;
  std::array<double, kJointCount> positive_work_limits_j =
      kPositiveWorkLimitsJ;
};

struct Result {
  bool valid = true;
  bool active = false;
  bool tremor_gate_enabled = false;
  bool tremor_gate_active = false;
  bool tremor_gate_triggered = false;
  double tremor_max_abs_dq_innovation_rad_s = 0.0;
  std::size_t tremor_gate_hold_ticks_remaining = 0;
  std::array<double, kJointCount> raw_q_target{};
  std::array<double, kJointCount> applied_q_target{};
  std::array<double, kJointCount> raw_torque_nm{};
  std::array<double, kJointCount> applied_torque_nm{};
  std::array<double, kJointCount> applied_positive_power_w{};
  std::array<double, kJointCount> positive_work_window_j{};
  std::array<std::uint8_t, kJointCount> intervention_mask{};
  std::size_t intervention_count = 0;
};

// Bit-level finite test remains valid when the deploy target is compiled with
// -ffast-math (which may otherwise optimize away std::isfinite checks).
inline bool IsFinite(double value) {
  constexpr std::uint64_t kExponentMask = UINT64_C(0x7ff0000000000000);
  return (onboard_bit_cast<std::uint64_t>(value) & kExponentMask) !=
         kExponentMask;
}

inline bool IsPositiveFinite(double value) {
  return IsFinite(value) && value > 0.0;
}

inline bool IsNonnegativeFinite(double value) {
  return IsFinite(value) && value >= 0.0;
}

inline bool IsValidConfig(const Config& config) {
  if (!IsPositiveFinite(config.dt) ||
      !IsPositiveFinite(config.envelope_scale) ||
      !IsPositiveFinite(config.tremor_trigger_threshold_rad_s) ||
      !IsPositiveFinite(config.tremor_peak_to_peak_threshold_rad_s) ||
      !IsNonnegativeFinite(config.tremor_recovery_damping_nm_per_rad_s) ||
      config.tremor_hold_ticks == 0) {
    return false;
  }
  for (std::size_t i = 0; i < kJointCount; ++i) {
    if (!IsPositiveFinite(config.effort_limits_nm[i]) ||
        !IsPositiveFinite(config.positive_power_limits_w[i]) ||
        !IsPositiveFinite(config.positive_work_limits_j[i])) {
      return false;
    }
  }
  return true;
}

class Governor {
 public:
  Governor() = default;

  explicit Governor(const Config& config) {
    SetConfig(config);
  }

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
    ResetWorkHistory();
    ResetTremorGate();
  }

  void ResetWorkHistory() {
    for (auto& sample : previous_positive_work_j_) {
      sample.fill(0.0);
    }
  }

  void ResetTremorGate() {
    for (auto& sample : tremor_dq_innovation_history_) {
      sample.fill(0.0);
    }
    previous_measured_dq_.fill(0.0);
    tremor_history_count_ = 0;
    tremor_history_cursor_ = 0;
    tremor_hold_ticks_remaining_ = 0;
    has_previous_measured_dq_ = false;
  }

  /**
   * Apply the envelope to one 50-Hz command.
   *
   * `enforce=false` produces pass-through diagnostics and clears the work
   * history.  The deploy uses that during the one-second startup blend so the
   * known-safe handoff is not altered and does not consume the work budget.
   */
  Result Apply(
      const std::array<double, kJointCount>& raw_q_target,
      const std::array<double, kJointCount>& measured_q,
      const std::array<double, kJointCount>& measured_dq,
      const std::array<double, kJointCount>& kp,
      const std::array<double, kJointCount>& kd,
      const std::array<double, kJointCount>& dq_target,
      const std::array<double, kJointCount>& tau_ff,
      bool enforce = true,
      bool external_tremor_trigger = false) {
    Result result;
    result.raw_q_target = raw_q_target;
    result.applied_q_target = raw_q_target;
    result.tremor_gate_enabled = config_.tremor_gate_enabled;

    if (!config_valid_) {
      result.valid = false;
      return result;
    }
    if (!std::all_of(measured_dq.begin(), measured_dq.end(), IsFinite)) {
      result.valid = false;
      return result;
    }

    const bool governor_requested = config_.enabled && enforce;
    if (!governor_requested) {
      ResetTremorGate();
    } else if (config_.tremor_gate_enabled) {
      UpdateTremorGate(measured_dq, external_tremor_trigger, result);
    }
    result.active =
        governor_requested &&
        (!config_.tremor_gate_enabled || result.tremor_gate_active);
    if (!result.active) {
      // A newly detected event starts with an unused energy budget.  Keep the
      // tremor detector history while the envelope itself is inactive.
      ResetWorkHistory();
    }

    std::array<double, kJointCount> current_positive_work_j{};
    for (std::size_t joint = 0; joint < kJointCount; ++joint) {
      const std::array<double, 7> inputs = {
          raw_q_target[joint], measured_q[joint], measured_dq[joint],
          kp[joint], kd[joint], dq_target[joint], tau_ff[joint]};
      if (!std::all_of(inputs.begin(), inputs.end(), IsFinite) ||
          kp[joint] <= 0.0) {
        result.valid = false;
        return result;
      }

      const double raw_tau =
          kp[joint] * (raw_q_target[joint] - measured_q[joint]) +
          kd[joint] * (dq_target[joint] - measured_dq[joint]) +
          tau_ff[joint];
      if (!IsFinite(raw_tau)) {
        result.valid = false;
        return result;
      }
      result.raw_torque_nm[joint] = raw_tau;

      double previous_work = 0.0;
      for (const auto& sample : previous_positive_work_j_) {
        previous_work += sample[joint];
      }

      double applied_tau = raw_tau;
      std::uint8_t mask = kNone;
      if (result.active) {
        const bool recovery_damping_joint =
            config_.tremor_recovery_damping_nm_per_rad_s > 0.0 &&
            std::find(kTremorJointIndices.begin(),
                      kTremorJointIndices.end(), joint) !=
                kTremorJointIndices.end();
        if (recovery_damping_joint && std::abs(measured_dq[joint]) > 1e-12) {
          applied_tau -=
              config_.tremor_recovery_damping_nm_per_rad_s *
              measured_dq[joint];
          mask |= kRecoveryDamping;
        }

        const double effort_limit = config_.effort_limits_nm[joint];
        const double effort_limited_tau =
            std::clamp(applied_tau, -effort_limit, effort_limit);
        if (std::abs(effort_limited_tau - applied_tau) > 1e-12) {
          mask |= kEffort;
          applied_tau = effort_limited_tau;
        }

        // A positive product injects energy.  Negative/zero power is braking
        // or neutral and intentionally bypasses both dynamic envelopes.
        const double requested_power = applied_tau * measured_dq[joint];
        if (requested_power > 0.0) {
          const double power_limit =
              config_.positive_power_limits_w[joint] *
              config_.envelope_scale;
          const double work_limit =
              config_.positive_work_limits_j[joint] *
              config_.envelope_scale;
          const double remaining_work =
              std::max(0.0, work_limit - previous_work);
          const double work_power_limit = remaining_work / config_.dt;
          const double allowed_power =
              std::min(power_limit, work_power_limit);

          if (requested_power > power_limit + 1e-12) {
            mask |= kPositivePower;
          }
          if (requested_power > work_power_limit + 1e-12) {
            mask |= kPositiveWork;
          }
          if (requested_power > allowed_power + 1e-12) {
            const double max_torque_magnitude =
                allowed_power / std::abs(measured_dq[joint]);
            applied_tau = std::copysign(
                std::min(std::abs(applied_tau), max_torque_magnitude),
                applied_tau);
          }
        }
      }

      const double applied_power = applied_tau * measured_dq[joint];
      const double positive_power = std::max(0.0, applied_power);
      const double current_work = positive_power * config_.dt;
      result.applied_torque_nm[joint] = applied_tau;
      result.applied_positive_power_w[joint] = positive_power;
      result.positive_work_window_j[joint] = previous_work + current_work;
      result.intervention_mask[joint] = mask;
      current_positive_work_j[joint] = result.active ? current_work : 0.0;

      if (mask != kNone) {
        ++result.intervention_count;
        const double applied_q =
            measured_q[joint] +
            (applied_tau - tau_ff[joint] -
             kd[joint] * (dq_target[joint] - measured_dq[joint])) /
                kp[joint];
        if (!IsFinite(applied_q)) {
          result.valid = false;
          return result;
        }
        result.applied_q_target[joint] = applied_q;
      }
    }

    if (result.active) {
      for (std::size_t sample = 0; sample + 1 < kPreviousWorkSamples;
           ++sample) {
        previous_positive_work_j_[sample] =
            previous_positive_work_j_[sample + 1];
      }
      previous_positive_work_j_[kPreviousWorkSamples - 1] =
          current_positive_work_j;
    }
    return result;
  }

 private:
  void UpdateTremorGate(
      const std::array<double, kJointCount>& measured_dq,
      bool external_trigger,
      Result& result) {
    bool trigger_candidate = external_trigger;
    if (!has_previous_measured_dq_) {
      previous_measured_dq_ = measured_dq;
      has_previous_measured_dq_ = true;
      FinalizeTremorGate(trigger_candidate, result);
      return;
    }

    const std::size_t newest_index = tremor_history_cursor_;
    auto& newest = tremor_dq_innovation_history_[newest_index];
    newest.fill(0.0);
    for (const std::size_t joint : kTremorJointIndices) {
      newest[joint] = measured_dq[joint] - previous_measured_dq_[joint];
      result.tremor_max_abs_dq_innovation_rad_s = std::max(
          result.tremor_max_abs_dq_innovation_rad_s,
          std::abs(newest[joint]));
    }
    previous_measured_dq_ = measured_dq;
    tremor_history_cursor_ =
        (tremor_history_cursor_ + 1) % kTremorHistorySamples;
    tremor_history_count_ =
        std::min(tremor_history_count_ + 1, kTremorHistorySamples);

    for (const std::size_t joint : kTremorJointIndices) {
      const double newest_innovation = newest[joint];
      const bool newest_positive_hit =
          newest_innovation >= config_.tremor_trigger_threshold_rad_s;
      const bool newest_negative_hit =
          newest_innovation <= -config_.tremor_trigger_threshold_rad_s;
      if (!newest_positive_hit && !newest_negative_hit) {
        continue;
      }
      double max_positive = newest_innovation;
      double min_negative = newest_innovation;
      for (std::size_t sample = 0; sample < tremor_history_count_; ++sample) {
        const double innovation = tremor_dq_innovation_history_[sample][joint];
        max_positive = std::max(max_positive, innovation);
        min_negative = std::min(min_negative, innovation);
      }
      const bool has_both_sides =
          max_positive >= config_.tremor_trigger_threshold_rad_s &&
          min_negative <= -config_.tremor_trigger_threshold_rad_s;
      const bool has_large_reversal =
          max_positive - min_negative >=
          config_.tremor_peak_to_peak_threshold_rad_s;
      if (has_both_sides && has_large_reversal) {
        trigger_candidate = true;
        break;
      }
    }

    FinalizeTremorGate(trigger_candidate, result);
  }

  void FinalizeTremorGate(bool trigger_candidate, Result& result) {
    // Do not continuously extend one event. A persistent event may trigger a
    // fresh bounded window only after the current recovery interval expires.
    const bool triggered =
        trigger_candidate && tremor_hold_ticks_remaining_ == 0;
    if (triggered) {
      tremor_hold_ticks_remaining_ = config_.tremor_hold_ticks;
    }
    result.tremor_gate_triggered = triggered;
    result.tremor_gate_active = tremor_hold_ticks_remaining_ > 0;
    result.tremor_gate_hold_ticks_remaining = tremor_hold_ticks_remaining_;
    if (tremor_hold_ticks_remaining_ > 0) {
      --tremor_hold_ticks_remaining_;
    }
  }

  Config config_{};
  bool config_valid_ = true;
  std::array<std::array<double, kJointCount>, kPreviousWorkSamples>
      previous_positive_work_j_{};
  std::array<std::array<double, kJointCount>, kTremorHistorySamples>
      tremor_dq_innovation_history_{};
  std::array<double, kJointCount> previous_measured_dq_{};
  std::size_t tremor_history_count_ = 0;
  std::size_t tremor_history_cursor_ = 0;
  std::size_t tremor_hold_ticks_remaining_ = 0;
  bool has_previous_measured_dq_ = false;
};

}  // namespace lower_body_energy_governor
