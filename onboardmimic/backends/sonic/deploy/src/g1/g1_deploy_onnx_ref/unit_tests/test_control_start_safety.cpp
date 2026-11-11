#include <array>
#include <limits>

#include <gtest/gtest.h>

#include "../include/control_start_safety.hpp"

namespace css = control_start_safety;

TEST(ControlStartSafety, PlannerStartCannotPassWithStaleReferenceMotion) {
  css::StartGate gate;
  css::GateInput input{
      .start_requested = true,
      .planner_required = true,
      .planner_enabled = true,
      .planner_initialized = true,
      .planner_motion_active = false,
      .measured_history_samples = 100,
  };

  EXPECT_EQ(gate.Update(input), css::GatePhase::WaitingForPlannerMotion);

  input.planner_motion_active = true;
  EXPECT_EQ(gate.Update(input), css::GatePhase::WarmingMeasuredHistory);
  input.measured_history_samples += css::kRequiredMeasuredHistorySamples - 1;
  EXPECT_EQ(gate.Update(input), css::GatePhase::WarmingMeasuredHistory);
  ++input.measured_history_samples;
  EXPECT_EQ(gate.Update(input), css::GatePhase::Ready);
}

TEST(ControlStartSafety, SamplesBeforePlannerMotionDoNotCountAsWarmup) {
  css::StartGate gate;
  css::GateInput input{
      .start_requested = true,
      .planner_required = true,
      .planner_enabled = false,
      .planner_initialized = false,
      .planner_motion_active = false,
      .measured_history_samples = 5,
  };

  EXPECT_EQ(gate.Update(input), css::GatePhase::WaitingForPlanner);
  input.measured_history_samples = 25;
  input.planner_enabled = true;
  input.planner_initialized = true;
  input.planner_motion_active = true;
  EXPECT_EQ(gate.Update(input), css::GatePhase::WarmingMeasuredHistory);
  input.measured_history_samples += css::kRequiredMeasuredHistorySamples;
  EXPECT_EQ(gate.Update(input), css::GatePhase::Ready);
}

TEST(ControlStartSafety, CancellingStartResetsMeasuredHistoryBaseline) {
  css::StartGate gate;
  css::GateInput input{
      .start_requested = true,
      .planner_required = true,
      .planner_enabled = true,
      .planner_initialized = true,
      .planner_motion_active = true,
      .measured_history_samples = 10,
  };

  EXPECT_EQ(gate.Update(input), css::GatePhase::WarmingMeasuredHistory);
  input.start_requested = false;
  EXPECT_EQ(gate.Update(input), css::GatePhase::Idle);

  input.start_requested = true;
  input.measured_history_samples = 100;
  EXPECT_EQ(gate.Update(input), css::GatePhase::WarmingMeasuredHistory);
}

TEST(ControlStartSafety, FirstPolicyCommandAcceptsFiniteContinuousTargets) {
  std::array<float, 29> held{};
  std::array<float, 29> target{};
  target[7] = 0.25F;
  target[18] = -0.5F;

  const auto result = css::CheckFirstCommandContinuity(held, target);
  EXPECT_TRUE(result.valid);
}

TEST(ControlStartSafety, FirstPolicyCommandRejectsLargeJump) {
  std::array<float, 29> held{};
  std::array<float, 29> target{};
  target[12] = static_cast<float>(css::kMaxFirstCommandDeltaRad + 0.01);

  const auto result = css::CheckFirstCommandContinuity(held, target);
  EXPECT_FALSE(result.valid);
  EXPECT_EQ(result.motor_index, 12U);
}

TEST(ControlStartSafety, FirstPolicyCommandRejectsNonFiniteTarget) {
  std::array<float, 29> held{};
  std::array<float, 29> target{};
  target[3] = std::numeric_limits<float>::quiet_NaN();

  const auto result = css::CheckFirstCommandContinuity(held, target);
  EXPECT_FALSE(result.valid);
  EXPECT_EQ(result.motor_index, 3U);
}

TEST(ControlStartSafety, StartupBlendBeginsNearHeldCommand) {
  std::array<float, 29> held{};
  std::array<float, 29> target{};
  target[4] = 0.87F;
  target[9] = -0.5F;

  const auto result = css::BlendCommandFromHeld(held, target, 1);
  EXPECT_TRUE(result.valid);
  EXPECT_NEAR(
      target[4],
      0.87F / static_cast<float>(css::kStartupBlendTicks),
      1e-6F);
  EXPECT_NEAR(
      target[9],
      -0.5F / static_cast<float>(css::kStartupBlendTicks),
      1e-6F);
}

TEST(ControlStartSafety, StartupBlendEndsAtCurrentPolicyTarget) {
  std::array<float, 29> held{};
  std::array<float, 29> target{};
  target[4] = 0.87F;
  target[9] = -0.5F;
  const auto expected = target;

  const auto result = css::BlendCommandFromHeld(
      held, target, css::kStartupBlendTicks);
  EXPECT_TRUE(result.valid);
  EXPECT_EQ(target, expected);
}

TEST(ControlStartSafety, StartupBlendRejectsNonFiniteTarget) {
  std::array<float, 29> held{};
  std::array<float, 29> target{};
  target[11] = std::numeric_limits<float>::infinity();

  const auto result = css::BlendCommandFromHeld(held, target, 1);
  EXPECT_FALSE(result.valid);
  EXPECT_EQ(result.motor_index, 11U);
}

TEST(ControlStartSafety, StartupBlendAllowsPlannerTargetToEvolvePastInitialGate) {
  std::array<float, 3> held{0.0F, 0.0F, 0.0F};
  std::array<float, 3> evolving_target{0.2F, -1.7F, 0.4F};

  const auto first_gate =
      css::CheckFirstCommandContinuity(held, evolving_target);
  EXPECT_FALSE(first_gate.valid);

  const auto blend =
      css::BlendCommandFromHeld(held, evolving_target, 25, 50);
  ASSERT_TRUE(blend.valid);
  EXPECT_FLOAT_EQ(evolving_target[0], 0.1F);
  EXPECT_FLOAT_EQ(evolving_target[1], -0.85F);
  EXPECT_FLOAT_EQ(evolving_target[2], 0.2F);
}
