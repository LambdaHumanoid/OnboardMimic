#include <array>
#include <chrono>
#include <limits>

#include <gtest/gtest.h>

#include "../include/brainco_hand_safety.hpp"

TEST(BraincoHandSafety, AcceptsOpenCloseAndContinuousCommands) {
    EXPECT_TRUE(brainco_hand_safety::IsCommandValid(
        {0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0}));
    EXPECT_TRUE(brainco_hand_safety::IsCommandValid(
        {0.5, 0.5, 1.0, 1.0, 1.0, 1.0, 0.0}));
    EXPECT_TRUE(brainco_hand_safety::IsCommandValid(
        {0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.0}));
    EXPECT_TRUE(brainco_hand_safety::IsCommandValid(
        brainco_hand_safety::kClampWireCommand));
}

TEST(BraincoHandSafety, ClampUsesThumbAgainstAllFourLongFingers) {
    const std::array<double, 7> expected = {
        0.5, 0.25, 0.5, 0.5, 0.5, 0.5, 0.0};
    EXPECT_EQ(brainco_hand_safety::kClampWireCommand, expected);
    EXPECT_GT(brainco_hand_safety::kClampWireCommand[0], 0.0);
    EXPECT_GT(brainco_hand_safety::kClampWireCommand[1], 0.0);
    for (std::size_t i = 2; i < 6; ++i) {
        EXPECT_GT(brainco_hand_safety::kClampWireCommand[i], 0.0);
    }
}

TEST(BraincoHandSafety, RejectsDex3FallbackPoses) {
    const std::array<double, 7> dex3_left_default = {
        0.0, 0.0, 1.75, -1.57, -1.75, -1.57, -1.75};
    const std::array<double, 7> dex3_right_default = {
        0.0, 0.0, -1.75, 1.57, 1.75, 1.57, 1.75};

    EXPECT_FALSE(brainco_hand_safety::IsCommandValid(dex3_left_default));
    EXPECT_FALSE(brainco_hand_safety::IsCommandValid(dex3_right_default));
}

TEST(BraincoHandSafety, RejectsNonFiniteAndOutOfRangeValues) {
    std::array<double, 7> command{};
    command[0] = 0.5001;
    EXPECT_FALSE(brainco_hand_safety::IsCommandValid(command));

    command.fill(0.0);
    command[2] = -0.001;
    EXPECT_FALSE(brainco_hand_safety::IsCommandValid(command));

    command.fill(0.0);
    command[4] = std::numeric_limits<double>::quiet_NaN();
    EXPECT_FALSE(brainco_hand_safety::IsCommandValid(command));
}

TEST(BraincoHandSafety, ResolvesUnavailableSidesToOpenIndependently) {
    const std::array<double, 7> close = {
        0.5, 0.5, 1.0, 1.0, 1.0, 1.0, 0.0};

    EXPECT_EQ(
        brainco_hand_safety::ResolveCommand(false, close),
        brainco_hand_safety::kOpenWireCommand);
    EXPECT_EQ(brainco_hand_safety::ResolveCommand(true, close), close);
}

TEST(BraincoHandSafety, ComputesMeanNormalizedClosureScore) {
    const std::array<double, 7> half_closed = {
        0.25, 0.25, 0.5, 0.5, 0.5, 0.5, 0.0};

    EXPECT_DOUBLE_EQ(
        brainco_hand_safety::NormalizedClosureScore(
            brainco_hand_safety::kOpenWireCommand),
        0.0);
    EXPECT_DOUBLE_EQ(
        brainco_hand_safety::NormalizedClosureScore(half_closed),
        0.5);
    EXPECT_DOUBLE_EQ(
        brainco_hand_safety::NormalizedClosureScore(
            brainco_hand_safety::kCloseWireCommand),
        1.0);
}

TEST(BraincoHandSafety, UsesStrictThresholdForBinaryClosure) {
    const std::array<double, 7> at_threshold = {
        0.25, 0.25, 0.5, 0.5, 0.5, 0.5, 0.0};
    const std::array<double, 7> above_threshold = {
        0.26, 0.26, 0.51, 0.51, 0.51, 0.51, 0.0};

    EXPECT_EQ(
        brainco_hand_safety::ResolveBinaryCommand(
            true, at_threshold, 0.5),
        brainco_hand_safety::kOpenWireCommand);
    EXPECT_EQ(
        brainco_hand_safety::ResolveBinaryCommand(
            true, above_threshold, 0.5),
        brainco_hand_safety::kCloseWireCommand);
}

TEST(BraincoHandSafety, BinaryResolverFailsOpenForInvalidInput) {
    auto invalid_command = brainco_hand_safety::kCloseWireCommand;
    invalid_command[2] = std::numeric_limits<double>::quiet_NaN();

    EXPECT_EQ(
        brainco_hand_safety::ResolveBinaryCommand(
            false, brainco_hand_safety::kCloseWireCommand, 0.5),
        brainco_hand_safety::kOpenWireCommand);
    EXPECT_EQ(
        brainco_hand_safety::ResolveBinaryCommand(true, invalid_command, 0.5),
        brainco_hand_safety::kOpenWireCommand);
    EXPECT_EQ(
        brainco_hand_safety::ResolveBinaryCommand(
            true, brainco_hand_safety::kCloseWireCommand, -0.1),
        brainco_hand_safety::kOpenWireCommand);
    EXPECT_EQ(
        brainco_hand_safety::ResolveBinaryCommand(
            true, brainco_hand_safety::kCloseWireCommand, 1.1),
        brainco_hand_safety::kOpenWireCommand);
}

TEST(BraincoHandSafety, ClampsSmallPolicyOvershootBeforeThresholding) {
    const std::array<double, 7> slightly_over_closed = {
        0.51, 0.52, 1.03, 1.04, 1.02, 1.01, 0.0};
    const std::array<double, 7> slightly_under_open = {
        -0.01, -0.02, -0.03, -0.04, -0.02, -0.01, 0.0};

    EXPECT_TRUE(brainco_hand_safety::IsBinaryPredictionValid(
        slightly_over_closed));
    EXPECT_TRUE(brainco_hand_safety::IsBinaryPredictionValid(
        slightly_under_open));
    EXPECT_DOUBLE_EQ(
        brainco_hand_safety::ClampedNormalizedClosureScore(
            slightly_over_closed),
        1.0);
    EXPECT_DOUBLE_EQ(
        brainco_hand_safety::ClampedNormalizedClosureScore(
            slightly_under_open),
        0.0);
    EXPECT_EQ(
        brainco_hand_safety::ResolveBinaryCommand(
            true, slightly_over_closed, 0.5),
        brainco_hand_safety::kCloseWireCommand);
    EXPECT_EQ(
        brainco_hand_safety::ResolveBinaryCommand(
            true, slightly_under_open, 0.5),
        brainco_hand_safety::kOpenWireCommand);
}

TEST(BraincoHandSafety, RejectsLargeFinitePredictionCorruption) {
    auto too_high = brainco_hand_safety::kCloseWireCommand;
    too_high[2] = 1.26;
    auto too_low = brainco_hand_safety::kOpenWireCommand;
    too_low[0] = -0.126;

    EXPECT_FALSE(brainco_hand_safety::IsBinaryPredictionValid(too_high));
    EXPECT_FALSE(brainco_hand_safety::IsBinaryPredictionValid(too_low));
    EXPECT_EQ(
        brainco_hand_safety::ResolveBinaryCommand(true, too_high, 0.5),
        brainco_hand_safety::kOpenWireCommand);
    EXPECT_EQ(
        brainco_hand_safety::ResolveBinaryCommand(true, too_low, 0.5),
        brainco_hand_safety::kOpenWireCommand);
}

TEST(BraincoHandSafety, ValidClosePassesThroughTimedClamp) {
    using namespace std::chrono_literals;
    brainco_hand_safety::StagedCommandState state;
    const auto start = std::chrono::steady_clock::time_point{} + 10s;

    EXPECT_EQ(
        brainco_hand_safety::ResolveStagedGraspCommand(
            true,
            brainco_hand_safety::kCloseWireCommand,
            0.5,
            300ms,
            state,
            start),
        brainco_hand_safety::kClampWireCommand);
    EXPECT_EQ(
        state.phase,
        brainco_hand_safety::StagedCommandPhase::Clamp);

    EXPECT_EQ(
        brainco_hand_safety::ResolveStagedGraspCommand(
            true,
            brainco_hand_safety::kCloseWireCommand,
            0.5,
            300ms,
            state,
            start + 299ms),
        brainco_hand_safety::kClampWireCommand);
    EXPECT_EQ(
        brainco_hand_safety::ResolveStagedGraspCommand(
            true,
            brainco_hand_safety::kCloseWireCommand,
            0.5,
            300ms,
            state,
            start + 300ms),
        brainco_hand_safety::kCloseWireCommand);
    EXPECT_EQ(
        state.phase,
        brainco_hand_safety::StagedCommandPhase::Curled);
}

TEST(BraincoHandSafety, OpeningIsImmediateAndResetsClosingSequence) {
    using namespace std::chrono_literals;
    brainco_hand_safety::StagedCommandState state;
    const auto start = std::chrono::steady_clock::time_point{} + 10s;

    (void)brainco_hand_safety::ResolveStagedGraspCommand(
        true,
        brainco_hand_safety::kCloseWireCommand,
        0.5,
        300ms,
        state,
        start);
    EXPECT_EQ(
        brainco_hand_safety::ResolveStagedGraspCommand(
            true,
            brainco_hand_safety::kOpenWireCommand,
            0.5,
            300ms,
            state,
            start + 100ms),
        brainco_hand_safety::kOpenWireCommand);
    EXPECT_EQ(state.phase, brainco_hand_safety::StagedCommandPhase::Open);

    EXPECT_EQ(
        brainco_hand_safety::ResolveStagedGraspCommand(
            true,
            brainco_hand_safety::kCloseWireCommand,
            0.5,
            300ms,
            state,
            start + 200ms),
        brainco_hand_safety::kClampWireCommand);
}

TEST(BraincoHandSafety, InvalidInputFailsOpenAndClearsClamp) {
    using namespace std::chrono_literals;
    brainco_hand_safety::StagedCommandState state;
    const auto start = std::chrono::steady_clock::time_point{} + 10s;
    auto invalid = brainco_hand_safety::kCloseWireCommand;
    invalid[2] = std::numeric_limits<double>::quiet_NaN();

    (void)brainco_hand_safety::ResolveStagedGraspCommand(
        true,
        brainco_hand_safety::kCloseWireCommand,
        0.5,
        300ms,
        state,
        start);
    EXPECT_EQ(
        brainco_hand_safety::ResolveStagedGraspCommand(
            true, invalid, 0.5, 300ms, state, start + 100ms),
        brainco_hand_safety::kOpenWireCommand);
    EXPECT_EQ(state.phase, brainco_hand_safety::StagedCommandPhase::Open);
}

TEST(BraincoHandSafety, ZeroClampDurationPreservesLegacyBinaryMode) {
    brainco_hand_safety::StagedCommandState state;
    EXPECT_EQ(
        brainco_hand_safety::ResolveStagedGraspCommand(
            true,
            brainco_hand_safety::kCloseWireCommand,
            0.5,
            std::chrono::milliseconds(0),
            state,
            std::chrono::steady_clock::time_point{}),
        brainco_hand_safety::kCloseWireCommand);
    EXPECT_EQ(
        state.phase,
        brainco_hand_safety::StagedCommandPhase::Curled);
}
