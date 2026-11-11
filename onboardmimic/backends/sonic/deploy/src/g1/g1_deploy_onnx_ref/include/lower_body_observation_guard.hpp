#include "onboard_bit_cast.hpp"
/**
 * @file lower_body_observation_guard.hpp
 * @brief Detect isolated, uncommanded knee/ankle velocity spikes and,
 *        optionally, reject them from policy observation history.
 */

#pragma once

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace lower_body_observation_guard {

inline constexpr std::size_t kJointCount = 29;
inline constexpr std::size_t kHistoryCapacity = 64;

// IsaacLab/policy-order indices corresponding to hardware-order
// L/R knee, ankle pitch, ankle roll.
inline constexpr std::array<std::size_t, 6> kMonitoredJointIndices = {
    9, 13, 17, 10, 14, 18};
inline constexpr std::array<std::size_t, 2> kAnkleRollJointIndices = {17, 18};

// Calibrated from the saved 2026-08-31 real-robot capture.  The conjunction
// catches the three uncommanded knee/ankle impacts in the bad interval and no
// events in the smooth real interval or the nominal MuJoCo replays.
inline constexpr double kDefaultInnovationThresholdRadS = 3.25;
inline constexpr double kDefaultMaxTargetDeltaRad = 0.05;
inline constexpr double kDefaultMaxPriorVirtualTorqueNm = 2.0;
inline constexpr std::size_t kDefaultRepairHoldTicks = 1;
inline constexpr double kDefaultRepairLowPassAlpha = 0.0;
inline constexpr double kDefaultRepairMaxPolicyDqStepRadS = 0.0;
inline constexpr double kDefaultRepairMaxPositionDqResidualRadS = 0.0;
inline constexpr double kDefaultControlDtS = 0.02;

struct Config {
  bool enabled = false;
  // Detection can be reused by a physical recovery mechanism while leaving
  // the policy observation untouched.  This repair remains an explicit,
  // separately controlled experiment because hiding a true energetic state
  // can make the closed-loop response worse.
  bool repair_observation = true;
  double innovation_threshold_rad_s = kDefaultInnovationThresholdRadS;
  double max_target_delta_rad = kDefaultMaxTargetDeltaRad;
  double max_prior_virtual_torque_nm =
      kDefaultMaxPriorVirtualTorqueNm;
  // Defaults preserve the original one-sample hold.  Experimental sustained
  // filtering can be enabled for a bounded number of policy ticks without
  // changing the raw velocity used by the physical PD and safety paths.
  std::size_t repair_hold_ticks = kDefaultRepairHoldTicks;
  double repair_low_pass_alpha = kDefaultRepairLowPassAlpha;
  // Optional recursive innovation clip in the policy-observation path.  Zero
  // preserves the existing low-pass behavior.  The measured velocity used by
  // physical PD and safety checks is never modified.
  double repair_max_policy_dq_step_rad_s =
      kDefaultRepairMaxPolicyDqStepRadS;
  // Optional detector-local consistency projection.  During a bounded repair
  // window, preserve the causal velocity supported by the measured position
  // change and bound only the residual reported by the velocity channel:
  //   dq_policy = dq_from_q + clamp(dq_raw - dq_from_q, +/- residual_limit).
  // Zero disables this mode.  Raw dq remains untouched for physical PD and
  // safety.  This targets the mixed real-robot signature where an actual foot
  // impact is accompanied by an exaggerated one-sample velocity estimate.
  double repair_max_position_dq_residual_rad_s =
      kDefaultRepairMaxPositionDqResidualRadS;
  double control_dt_s = kDefaultControlDtS;
  // Apply the recursive innovation bound independently on every monitored
  // policy-dq sample, without opening a detector-driven hold window.  This is
  // the exact form evaluated offline on the saved real capture: values inside
  // the bound pass through bit-for-bit and only actual clips count as repair.
  bool continuous_innovation_clip = false;
  // After a detector-qualified event, keep clipping only the selected joint's
  // recursive policy-dq innovation until the raw value is reachable in one
  // bounded step.  This avoids both an arbitrary hold duration and the
  // discontinuous snap-back produced by a one-tick repair.
  bool repair_until_caught_up = false;
  bool repair_all_monitored_joints = false;
  // Middle scope between one triggering joint and all six monitored joints.
  // The saved real capture's qualifying events were confined to ankle roll.
  bool repair_both_ankle_roll_joints = false;
  // By default a new qualifying event refreshes the repair duration.  The
  // fixed-window experiment disables that refresh so one impact can open at
  // most one bounded window; later spikes inside it are still repaired but
  // cannot prolong policy-observation lag.
  bool extend_repair_window_on_retrigger = true;
  // The saved real capture's 14 production-detector events are all ankle-roll
  // impacts.  Keep this restriction optional so the generic detector retains
  // its prior behavior unless an experiment explicitly selects that signature.
  bool trigger_ankle_roll_only = false;
};

struct Result {
  bool valid = true;
  bool enabled = false;
  bool repair_enabled = false;
  bool active = false;
  bool triggered = false;
  double max_abs_dq_innovation_rad_s = 0.0;
  std::array<double, kJointCount> policy_dq{};
  std::array<std::uint8_t, kJointCount> event_mask{};
  std::array<std::uint8_t, kJointCount> repair_mask{};
  std::size_t event_count = 0;
  std::size_t repair_count = 0;
};

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
  return IsPositiveFinite(config.innovation_threshold_rad_s) &&
         IsNonnegativeFinite(config.max_target_delta_rad) &&
         IsNonnegativeFinite(config.max_prior_virtual_torque_nm) &&
         config.repair_hold_ticks > 0 &&
         IsNonnegativeFinite(config.repair_low_pass_alpha) &&
         config.repair_low_pass_alpha <= 1.0 &&
         IsNonnegativeFinite(config.repair_max_policy_dq_step_rad_s) &&
         IsNonnegativeFinite(
             config.repair_max_position_dq_residual_rad_s) &&
         IsPositiveFinite(config.control_dt_s) &&
         (!config.continuous_innovation_clip ||
          config.repair_max_policy_dq_step_rad_s > 0.0) &&
         (!config.repair_until_caught_up ||
          config.repair_max_policy_dq_step_rad_s > 0.0) &&
         !(config.continuous_innovation_clip &&
           config.repair_until_caught_up) &&
         !(config.repair_max_position_dq_residual_rad_s > 0.0 &&
           (config.repair_max_policy_dq_step_rad_s > 0.0 ||
            config.continuous_innovation_clip ||
            config.repair_until_caught_up));
}

class Guard {
 public:
  Guard() = default;

  explicit Guard(const Config& config) { SetConfig(config); }

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
    has_previous_ = false;
    previous_q_.fill(0.0);
    previous_raw_dq_.fill(0.0);
    previous_policy_dq_.fill(0.0);
    previous_q_target_.fill(0.0);
    repair_ticks_remaining_.fill(0);
    history_count_ = 0;
    history_cursor_ = 0;
    for (auto& record : history_) {
      record.valid = false;
      record.index = 0;
      record.policy_dq.fill(0.0);
    }
  }

  Result Update(
      std::uint64_t index,
      const std::array<double, kJointCount>& measured_q,
      const std::array<double, kJointCount>& measured_dq,
      const std::array<double, kJointCount>& q_target,
      const std::array<double, kJointCount>& kp,
      const std::array<double, kJointCount>& kd,
      bool enforce = true) {
    Result result;
    result.enabled = config_.enabled;
    result.repair_enabled = config_.enabled && config_.repair_observation;
    result.active = config_.enabled && enforce;
    result.policy_dq = measured_dq;

    if (!config_valid_ ||
        !AllFinite(measured_q) || !AllFinite(measured_dq) ||
        !AllFinite(q_target) || !AllFinite(kp) || !AllFinite(kd)) {
      result.valid = false;
      Store(index, result.policy_dq);
      has_previous_ = false;
      repair_ticks_remaining_.fill(0);
      return result;
    }

    if (!result.active) {
      // Never carry an experimental repair window through startup, pause, or
      // another non-CONTROL state.  Continue warming raw history below.
      repair_ticks_remaining_.fill(0);
    }

    const bool repair_window_active_at_update = std::any_of(
        repair_ticks_remaining_.begin(), repair_ticks_remaining_.end(),
        [](std::size_t ticks) { return ticks != 0; });

    if (has_previous_) {
      for (const std::size_t joint : kMonitoredJointIndices) {
        const double innovation =
            measured_dq[joint] - previous_raw_dq_[joint];
        result.max_abs_dq_innovation_rad_s = std::max(
            result.max_abs_dq_innovation_rad_s, std::abs(innovation));
        if (!result.active) {
          continue;
        }
        if (config_.trigger_ankle_roll_only &&
            std::find(kAnkleRollJointIndices.begin(),
                      kAnkleRollJointIndices.end(), joint) ==
                kAnkleRollJointIndices.end()) {
          continue;
        }

        const double target_delta =
            std::abs(q_target[joint] - previous_q_target_[joint]);
        const double prior_virtual_torque =
            kp[joint] * (q_target[joint] - previous_q_[joint]) -
            kd[joint] * previous_raw_dq_[joint];
        if (std::abs(innovation) >=
                config_.innovation_threshold_rad_s &&
            target_delta <= config_.max_target_delta_rad &&
            std::abs(prior_virtual_torque) <=
                config_.max_prior_virtual_torque_nm) {
          result.event_mask[joint] = 1;
          ++result.event_count;
          if (config_.repair_observation &&
              !config_.continuous_innovation_clip &&
              (config_.extend_repair_window_on_retrigger ||
               !repair_window_active_at_update)) {
            if (config_.repair_all_monitored_joints) {
              for (const std::size_t monitored_joint :
                   kMonitoredJointIndices) {
                repair_ticks_remaining_[monitored_joint] =
                    config_.repair_until_caught_up
                        ? 1
                        : config_.repair_hold_ticks;
              }
            } else if (config_.repair_both_ankle_roll_joints) {
              for (const std::size_t ankle_roll_joint :
                   kAnkleRollJointIndices) {
                repair_ticks_remaining_[ankle_roll_joint] =
                    config_.repair_until_caught_up
                        ? 1
                        : config_.repair_hold_ticks;
              }
            } else {
              repair_ticks_remaining_[joint] =
                  config_.repair_until_caught_up
                      ? 1
                      : config_.repair_hold_ticks;
            }
          }
        }
      }
    }
    result.triggered = result.event_count != 0;

    if (result.active && config_.repair_observation && has_previous_) {
      for (const std::size_t joint : kMonitoredJointIndices) {
        const bool scheduled_repair =
            repair_ticks_remaining_[joint] != 0;
        if (!scheduled_repair && !config_.continuous_innovation_clip) {
          continue;
        }
        if (config_.repair_max_position_dq_residual_rad_s > 0.0) {
          // Keep the component corroborated by the causal position change and
          // bound only disagreement from the instantaneous velocity estimate.
          // This does not create recursive lag: every repaired sample is
          // independently anchored to q(t)-q(t-1).
          const double position_dq =
              (measured_q[joint] - previous_q_[joint]) /
              config_.control_dt_s;
          const double residual = measured_dq[joint] - position_dq;
          const double limit =
              config_.repair_max_position_dq_residual_rad_s;
          result.policy_dq[joint] = position_dq +
              std::clamp(residual, -limit, limit);
        } else if (config_.repair_max_policy_dq_step_rad_s > 0.0) {
          // Bound only the new information entering the policy's recursive dq
          // history.  Unlike a low-pass, this follows normal motion exactly
          // whenever its per-tick innovation is inside the calibrated bound.
          const double innovation =
              measured_dq[joint] - previous_policy_dq_[joint];
          const double limit = config_.repair_max_policy_dq_step_rad_s;
          result.policy_dq[joint] = previous_policy_dq_[joint] +
              std::clamp(innovation, -limit, limit);
        } else {
          // One-pole low-pass in only the policy observation path.  Alpha=0
          // and hold_ticks=1 reproduce the original single-sample hold.
          result.policy_dq[joint] =
              previous_policy_dq_[joint] +
              config_.repair_low_pass_alpha *
                  (measured_dq[joint] - previous_policy_dq_[joint]);
        }
        const bool policy_value_changed =
            result.policy_dq[joint] != measured_dq[joint];
        if ((scheduled_repair && !config_.repair_until_caught_up) ||
            policy_value_changed) {
          result.repair_mask[joint] = 1;
          ++result.repair_count;
        }
        if (scheduled_repair) {
          if (config_.repair_until_caught_up) {
            repair_ticks_remaining_[joint] = policy_value_changed ? 1 : 0;
          } else {
            --repair_ticks_remaining_[joint];
          }
        }
      }
    }

    previous_q_ = measured_q;
    previous_raw_dq_ = measured_dq;
    previous_policy_dq_ = result.policy_dq;
    previous_q_target_ = q_target;
    has_previous_ = true;
    Store(index, result.policy_dq);
    return result;
  }

  bool Lookup(
      std::uint64_t index,
      std::array<double, kJointCount>& policy_dq) const {
    for (std::size_t offset = 0; offset < history_count_; ++offset) {
      const std::size_t slot =
          (history_cursor_ + kHistoryCapacity - 1 - offset) %
          kHistoryCapacity;
      const HistoryRecord& record = history_[slot];
      if (record.valid && record.index == index) {
        policy_dq = record.policy_dq;
        return true;
      }
    }
    return false;
  }

 private:
  struct HistoryRecord {
    bool valid = false;
    std::uint64_t index = 0;
    std::array<double, kJointCount> policy_dq{};
  };

  static bool AllFinite(
      const std::array<double, kJointCount>& values) {
    return std::all_of(values.begin(), values.end(), IsFinite);
  }

  void Store(
      std::uint64_t index,
      const std::array<double, kJointCount>& policy_dq) {
    HistoryRecord& record = history_[history_cursor_];
    record.valid = true;
    record.index = index;
    record.policy_dq = policy_dq;
    history_cursor_ = (history_cursor_ + 1) % kHistoryCapacity;
    history_count_ = std::min(history_count_ + 1, kHistoryCapacity);
  }

  Config config_{};
  bool config_valid_ = true;
  bool has_previous_ = false;
  std::array<double, kJointCount> previous_q_{};
  std::array<double, kJointCount> previous_raw_dq_{};
  std::array<double, kJointCount> previous_policy_dq_{};
  std::array<double, kJointCount> previous_q_target_{};
  std::array<std::size_t, kJointCount> repair_ticks_remaining_{};
  std::array<HistoryRecord, kHistoryCapacity> history_{};
  std::size_t history_count_ = 0;
  std::size_t history_cursor_ = 0;
};

}  // namespace lower_body_observation_guard
