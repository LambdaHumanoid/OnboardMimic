/**
 * @file dummy_hands.hpp
 * @brief No-op hand implementation (when --hand-type none).
 */

#pragma once

#include <array>

#include "hands_interface.hpp"

class DummyHands : public HandsBase {
public:
    void initialize(const std::string&) override {}
    void setZmqChannel(ZmqRobotChannel*) override {}
    void writeOnce() override {}
    void open(bool, double, double) override {}
    void close(bool, double, double) override {}
    void setAllJointsCommand(bool, const std::array<double, 7>&) override {}
    void SetMaxCloseRatio(double) override {}
    double GetMaxCloseRatio() const override { return 1.0; }
    std::shared_ptr<const unitree_hg::msg::dds_::HandState_> getState(bool) const override {
        return nullptr;
    }
};
