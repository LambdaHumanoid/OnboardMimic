/**
 * @file hands_interface.hpp
 * @brief Abstract hand driver for SONIC C++ deploy.
 *
 * Three implementations selected by --hand-type at startup:
 *   - DummyHands  : no hardware, all ops no-op
 *   - Dex3Hands   : Unitree Dex3-1, 7-DOF, original implementation
 *   - BraincoHands: BrainCo Revo2, 6-DOF command via ZMQ (staged policy in G1Deploy)
 *
 * Upstream g1_deploy_onnx_ref code uses `hands_->X()` polymorphically without
 * caring about the underlying hand type; DummyHands' no-ops mean we drop
 * `if (!disable_hands_)` guards everywhere.
 */

#pragma once

#include <array>
#include <memory>
#include <string>
#include <vector>

#include <unitree/idl/hg/HandState_.hpp>

class ZmqRobotChannel;

class HandsBase {
public:
    virtual ~HandsBase() = default;

    /// One-time HW initialization. For Dex3 this opens DDS channels;
    /// brainco/dummy are no-ops.
    virtual void initialize(const std::string& networkInterface) {}

    /// Wire to the shared ZMQ robot channel (brainco sends cmds through here).
    virtual void setZmqChannel(ZmqRobotChannel* /*ch*/) {}

    /// Periodic high-rate write (called per control tick).
    /// Dex3 sends buffered cmd; brainco no-op (cmds sent immediately on open/close).
    virtual void writeOnce() {}

    /// Snap to "open" pose. is_left selects hand.
    virtual void open(bool is_left, double kp = 1.5, double kd = 0.1) {}

    /// Snap to "closed" pose.
    virtual void close(bool is_left, double kp = 1.5, double kd = 0.1) {}

    /// Per-joint target (fixed 7-DOF upstream convention; brainco uses first 6).
    /// Match Dex3Hands' existing call sites that pass std::array<double, 7>.
    virtual void setAllJointsCommand(bool is_left, const std::array<double, 7>& q) {}

    /// Close ratio knob (Dex3 only; others ignore).
    virtual void SetMaxCloseRatio(double ratio) {}
    virtual double GetMaxCloseRatio() const { return 1.0; }

    /// Get last received HandState_ (Dex3 only; brainco/dummy return nullptr).
    /// Callers must null-check.
    virtual std::shared_ptr<const unitree_hg::msg::dds_::HandState_> getState(bool is_left) const {
        return nullptr;
    }

    /// Return current joint feedback in the fixed 7-value SONIC logger schema.
    /// Dex3 uses its native HandState_; BrainCo overrides this to consume its
    /// six physical MotorStatus values plus one zero compatibility slot.
    virtual bool getJointState(
        bool is_left,
        std::array<double, 7>& q,
        std::array<double, 7>& dq) const {
        q.fill(0.0);
        dq.fill(0.0);
        const auto state = getState(is_left);
        if (!state || state->motor_state().size() < 7) return false;
        for (std::size_t i = 0; i < 7; ++i) {
            q[i] = state->motor_state()[i].q();
            dq[i] = state->motor_state()[i].dq();
        }
        return true;
    }
};
