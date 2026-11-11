/**
 * @file brainco_hands.hpp
 * @brief BrainCo Revo2 6-DOF command transport for SONIC C++ deploy.
 *
 * Sends ZMQ messages on `rt/brainco/{left,right}/cmd` (msgpack ZmqJointCmd
 * 6-floats) through the shared ZmqRobotChannel. Robot-side run_zmq_dds_bridge.py
 * forwards them to /joint_commands_{left,right}; stark_node drives USB-485.
 *
 * Command order:
 *   [thumb, thumb_aux, index, middle, ring, pinky]
 * Ranges:
 *   [0..0.5, 0..0.5, 0..1, 0..1, 0..1, 0..1]
 * G1Deploy thresholds valid upstream targets and stages closure through the
 * canonical thumb-versus-four-finger clamp before every finger curls.
 */

#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <iostream>
#include <vector>

#include "brainco_hand_safety.hpp"
#include "hands_interface.hpp"
#include "zmq_robot_channel.hpp"

class BraincoHands : public HandsBase {
public:
    static constexpr auto OPEN_CMD = brainco_hand_safety::kOpenCommand;
    static constexpr auto CLAMP_CMD = brainco_hand_safety::kClampCommand;
    static constexpr auto CLOSE_CMD = brainco_hand_safety::kCloseCommand;

    void initialize(const std::string&) override {
        std::cout << "[BraincoHands] initialized (cmds go through ZMQ on setZmqChannel)" << std::endl;
    }

    void setZmqChannel(ZmqRobotChannel* ch) override { zmq_channel_ = ch; }

    void writeOnce() override {
        // No-op. Brainco cmds are sent immediately in open/close/setAllJointsCommand.
    }

    void open(bool is_left, double, double) override { send_(is_left, OPEN_CMD); }

    void close(bool is_left, double, double) override { send_(is_left, CLOSE_CMD); }

    void setAllJointsCommand(bool is_left, const std::array<double, 7>& q) override {
        // Fail the whole side open when the upstream payload is malformed.  In
        // particular, InputInterface's no-data fallback is a signed Dex3 pose;
        // clamping that pose joint-by-joint would close an arbitrary subset of
        // Revo2 fingers.
        if (!brainco_hand_safety::IsCommandValid(q)) {
            send_(is_left, OPEN_CMD);
            return;
        }

        std::array<double, 6> cmd = OPEN_CMD;
        for (std::size_t i = 0; i < cmd.size(); ++i) {
            cmd[i] = q[i];
        }
        send_(is_left, cmd);
    }

    bool getJointState(
        bool is_left,
        std::array<double, 7>& q,
        std::array<double, 7>& dq) const override {
        if (!zmq_channel_) {
            q.fill(0.0);
            dq.fill(0.0);
            return false;
        }
        return zmq_channel_->GetBraincoJointState(is_left, q, dq);
    }

private:
    ZmqRobotChannel* zmq_channel_ = nullptr;

    void send_(bool is_left, const std::array<double, 6>& cmd) {
        if (!zmq_channel_) {
            std::cerr << "[BraincoHands] ERROR: zmq_channel_ not set" << std::endl;
            return;
        }
        zmq_channel_->PublishBraincoCmd(is_left, cmd);
    }
};
