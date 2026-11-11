#include "onboard_bit_cast.hpp"
#ifndef CONTROL_START_SAFETY_HPP
#define CONTROL_START_SAFETY_HPP

#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>

namespace control_start_safety {

inline constexpr std::size_t kRequiredMeasuredHistorySamples = 10;
inline constexpr double kMaxFirstCommandDeltaRad = 1.5;
inline constexpr std::size_t kStartupBlendTicks = 50;

enum class GatePhase {
  Idle,
  WaitingForPlanner,
  WaitingForPlannerMotion,
  WarmingMeasuredHistory,
  Ready,
};

struct GateInput {
  bool start_requested = false;
  bool planner_required = false;
  bool planner_enabled = false;
  bool planner_initialized = false;
  bool planner_motion_active = false;
  std::size_t measured_history_samples = 0;
};

/**
 * @brief Stateful gate for the WAIT_FOR_CONTROL -> CONTROL transition.
 *
 * The baseline is intentionally captured only after the requested planner
 * motion is active.  Samples logged while the old reference motion is still
 * installed therefore cannot satisfy the measured-history warmup.
 */
class StartGate {
 public:
  GatePhase Update(const GateInput& input) {
    if (!input.start_requested) {
      Reset();
      return phase_;
    }

    if (input.planner_required) {
      if (!input.planner_enabled || !input.planner_initialized) {
        history_baseline_valid_ = false;
        phase_ = GatePhase::WaitingForPlanner;
        return phase_;
      }
      if (!input.planner_motion_active) {
        history_baseline_valid_ = false;
        phase_ = GatePhase::WaitingForPlannerMotion;
        return phase_;
      }
    }

    if (!history_baseline_valid_) {
      history_baseline_ = input.measured_history_samples;
      history_baseline_valid_ = true;
    }

    const std::size_t samples_since_baseline =
        input.measured_history_samples >= history_baseline_
            ? input.measured_history_samples - history_baseline_
            : 0;
    phase_ =
        samples_since_baseline >= kRequiredMeasuredHistorySamples
            ? GatePhase::Ready
            : GatePhase::WarmingMeasuredHistory;
    return phase_;
  }

  void Reset() {
    phase_ = GatePhase::Idle;
    history_baseline_ = 0;
    history_baseline_valid_ = false;
  }

  GatePhase phase() const { return phase_; }

 private:
  GatePhase phase_ = GatePhase::Idle;
  std::size_t history_baseline_ = 0;
  bool history_baseline_valid_ = false;
};

inline const char* PhaseName(GatePhase phase) {
  switch (phase) {
    case GatePhase::Idle:
      return "idle";
    case GatePhase::WaitingForPlanner:
      return "waiting for planner initialization";
    case GatePhase::WaitingForPlannerMotion:
      return "waiting for planner_motion installation";
    case GatePhase::WarmingMeasuredHistory:
      return "warming measured robot-state history";
    case GatePhase::Ready:
      return "ready";
  }
  return "unknown";
}

inline bool IsFinite(double value) {
  const std::uint64_t bits = onboard_bit_cast<std::uint64_t>(value);
  return (bits & UINT64_C(0x7ff0000000000000)) !=
         UINT64_C(0x7ff0000000000000);
}

struct ContinuityResult {
  bool valid = true;
  std::size_t motor_index = 0;
  double delta_rad = 0.0;
};

struct BlendResult {
  bool valid = true;
  std::size_t motor_index = 0;
};

/**
 * @brief Validate the first policy target before it reaches the writer thread.
 *
 * A bit-level finite check is used because the deploy is compiled with
 * -ffast-math, under which std::isfinite checks may be optimized away.
 */
template <typename StartValue, typename TargetValue, std::size_t N>
ContinuityResult CheckFirstCommandContinuity(
    const std::array<StartValue, N>& held_targets,
    const std::array<TargetValue, N>& policy_targets,
    double max_delta_rad = kMaxFirstCommandDeltaRad) {
  ContinuityResult result;
  if (!IsFinite(max_delta_rad) || max_delta_rad <= 0.0) {
    result.valid = false;
    return result;
  }

  for (std::size_t i = 0; i < N; ++i) {
    const double held = static_cast<double>(held_targets[i]);
    const double target = static_cast<double>(policy_targets[i]);
    if (!IsFinite(held) || !IsFinite(target)) {
      result.valid = false;
      result.motor_index = i;
      result.delta_rad = target - held;
      return result;
    }
    const double signed_delta = target - held;
    const double delta = signed_delta < 0.0 ? -signed_delta : signed_delta;
    if (delta > max_delta_rad) {
      result.valid = false;
      result.motor_index = i;
      result.delta_rad = delta;
      return result;
    }
  }
  return result;
}

/**
 * @brief Blend one policy command from the command held before CONTROL.
 *
 * The final blend tick has alpha=1 and therefore publishes the current policy
 * target exactly; there is no deferred limiter remainder that can jump on the
 * following tick. Non-finite values and invalid progress fail closed.
 */
template <typename HeldValue, typename TargetValue, std::size_t N>
BlendResult BlendCommandFromHeld(
    const std::array<HeldValue, N>& held_targets,
    std::array<TargetValue, N>& policy_targets,
    std::size_t completed_ticks,
    std::size_t total_ticks = kStartupBlendTicks) {
  BlendResult result;
  if (total_ticks == 0 || completed_ticks == 0 ||
      completed_ticks > total_ticks) {
    result.valid = false;
    return result;
  }

  const double alpha =
      static_cast<double>(completed_ticks) /
      static_cast<double>(total_ticks);
  for (std::size_t i = 0; i < N; ++i) {
    const double held = static_cast<double>(held_targets[i]);
    const double target = static_cast<double>(policy_targets[i]);
    if (!IsFinite(held) || !IsFinite(target)) {
      result.valid = false;
      result.motor_index = i;
      return result;
    }
    policy_targets[i] =
        static_cast<TargetValue>(held + alpha * (target - held));
  }
  return result;
}

}  // namespace control_start_safety

#endif  // CONTROL_START_SAFETY_HPP
