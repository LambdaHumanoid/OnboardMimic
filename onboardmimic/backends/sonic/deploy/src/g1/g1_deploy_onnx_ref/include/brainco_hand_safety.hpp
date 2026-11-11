#include "onboard_bit_cast.hpp"
#pragma once

#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cstddef>
#include <cstdint>

namespace brainco_hand_safety {

inline constexpr std::array<double, 6> kOpenCommand = {
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
inline constexpr std::array<double, 6> kCloseCommand = {
    0.5, 0.5, 1.0, 1.0, 1.0, 1.0};
// Treat the Revo2 as a two-jaw gripper during initial capture: the thumb is
// one jaw and the four long fingers form the opposing jaw.  The metacarpal is
// fully opposed, thumb flexion is partial, and the four long fingers move to
// half closure before every finger continues into kCloseCommand.
inline constexpr std::array<double, 6> kClampCommand = {
    0.5, 0.25, 0.5, 0.5, 0.5, 0.5};
inline constexpr std::array<double, 7> kOpenWireCommand = {
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
inline constexpr std::array<double, 7> kCloseWireCommand = {
    0.5, 0.5, 1.0, 1.0, 1.0, 1.0, 0.0};
inline constexpr std::array<double, 7> kClampWireCommand = {
    0.5, 0.25, 0.5, 0.5, 0.5, 0.5, 0.0};
// Policy outputs are continuous regressions and can legitimately overshoot a
// training-set endpoint slightly.  Accept at most 25% normalized overshoot,
// then clamp before thresholding; larger values still fail open as malformed.
inline constexpr double kBinaryPredictionTolerance = 0.25;

inline bool IsFinite(double value) {
    // This target is built with fast-math, where std::isfinite/NaN comparisons
    // may be optimized under the assumption that non-finite values do not
    // exist.  Inspect IEEE-754 exponent bits so malformed wire data still
    // fails open.
    constexpr std::uint64_t kExponentMask = UINT64_C(0x7ff0000000000000);
    return (onboard_bit_cast<std::uint64_t>(value) & kExponentMask) != kExponentMask;
}

inline bool IsCommandValid(const std::array<double, 7>& command) {
    for (std::size_t i = 0; i < kOpenCommand.size(); ++i) {
        if (!IsFinite(command[i]) ||
            command[i] < kOpenCommand[i] ||
            command[i] > kCloseCommand[i]) {
            return false;
        }
    }
    return true;
}

inline bool IsBinaryPredictionValid(const std::array<double, 7>& command) {
    for (std::size_t i = 0; i < kOpenCommand.size(); ++i) {
        if (!IsFinite(command[i])) {
            return false;
        }
        const double normalized = command[i] / kCloseCommand[i];
        if (normalized < -kBinaryPredictionTolerance ||
            normalized > 1.0 + kBinaryPredictionTolerance) {
            return false;
        }
    }
    return true;
}

inline std::array<double, 7> ResolveCommand(
    bool has_data, const std::array<double, 7>& command) {
    return has_data && IsCommandValid(command) ? command : kOpenWireCommand;
}

inline bool IsThresholdValid(double threshold) {
    return IsFinite(threshold) && threshold >= 0.0 && threshold <= 1.0;
}

inline double NormalizedClosureScore(const std::array<double, 7>& command) {
    double sum = 0.0;
    for (std::size_t i = 0; i < kCloseCommand.size(); ++i) {
        sum += command[i] / kCloseCommand[i];
    }
    return sum / static_cast<double>(kCloseCommand.size());
}

inline double ClampedNormalizedClosureScore(
    const std::array<double, 7>& command) {
    double sum = 0.0;
    for (std::size_t i = 0; i < kCloseCommand.size(); ++i) {
        sum += std::clamp(command[i] / kCloseCommand[i], 0.0, 1.0);
    }
    return sum / static_cast<double>(kCloseCommand.size());
}

inline bool ShouldClose(
    const std::array<double, 7>& command, double threshold) {
    return IsThresholdValid(threshold) &&
           IsBinaryPredictionValid(command) &&
           ClampedNormalizedClosureScore(command) > threshold;
}

inline std::array<double, 7> ResolveBinaryCommand(
    bool has_data,
    const std::array<double, 7>& command,
    double threshold) {
    return has_data && ShouldClose(command, threshold)
        ? kCloseWireCommand
        : kOpenWireCommand;
}

enum class StagedCommandPhase {
    Open,
    Clamp,
    Curled,
};

struct StagedCommandState {
    StagedCommandPhase phase = StagedCommandPhase::Open;
    std::chrono::steady_clock::time_point clamp_started{};
};

inline const char* PhaseName(StagedCommandPhase phase) {
    switch (phase) {
        case StagedCommandPhase::Open:
            return "OPEN";
        case StagedCommandPhase::Clamp:
            return "CLAMP";
        case StagedCommandPhase::Curled:
            return "CURL";
    }
    return "UNKNOWN";
}

inline void ResetStagedCommand(StagedCommandState& state) {
    state.phase = StagedCommandPhase::Open;
    state.clamp_started = {};
}

// Preserve the checkpoint's existing binary intent, but make every valid
// OPEN->CURL transition pass through a timed two-jaw clamp. Opening and all
// malformed/unavailable inputs remain immediate fail-open operations.
inline std::array<double, 7> ResolveStagedGraspCommand(
    bool has_data,
    const std::array<double, 7>& command,
    double threshold,
    std::chrono::milliseconds clamp_duration,
    StagedCommandState& state,
    std::chrono::steady_clock::time_point now) {
    if (!has_data || !ShouldClose(command, threshold)) {
        ResetStagedCommand(state);
        return kOpenWireCommand;
    }

    if (clamp_duration.count() <= 0) {
        state.phase = StagedCommandPhase::Curled;
        return kCloseWireCommand;
    }

    if (state.phase == StagedCommandPhase::Open) {
        state.phase = StagedCommandPhase::Clamp;
        state.clamp_started = now;
        return kClampWireCommand;
    }

    if (state.phase == StagedCommandPhase::Clamp) {
        if (now - state.clamp_started < clamp_duration) {
            return kClampWireCommand;
        }
        state.phase = StagedCommandPhase::Curled;
    }

    return kCloseWireCommand;
}

}  // namespace brainco_hand_safety
