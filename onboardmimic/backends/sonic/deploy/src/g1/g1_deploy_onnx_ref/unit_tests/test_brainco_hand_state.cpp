#include <array>
#include <map>
#include <string>

#include <gtest/gtest.h>
#include <msgpack.hpp>

#include "../include/brainco_hand_state.hpp"

namespace {

msgpack::sbuffer PackStatus(
    const std::array<int, 6>& positions,
    const std::array<int, 6>& speeds) {
    msgpack::sbuffer buffer;
    msgpack::packer<msgpack::sbuffer> packer(&buffer);
    packer.pack_map(4);
    packer.pack("positions");
    packer.pack(positions);
    packer.pack("speeds");
    packer.pack(speeds);
    packer.pack("currents");
    packer.pack(std::array<int, 6>{0, 0, 0, 0, 0, 0});
    packer.pack("states");
    packer.pack(std::array<int, 6>{0, 0, 0, 0, 0, 0});
    return buffer;
}

}  // namespace

TEST(BraincoHandState, DecodesAndNormalizesPhysicalFeedback) {
    const auto payload = PackStatus(
        {400, 395, 49, 50, 50, 49},
        {1000, -1000, 100, -100, 0, 1});

    brainco_hand_state::JointState state;
    std::string error;
    ASSERT_TRUE(brainco_hand_state::DecodeMotorStatus(
        payload.data(), payload.size(), state, &error)) << error;

    EXPECT_DOUBLE_EQ(state.q[0], 0.4);
    EXPECT_DOUBLE_EQ(state.q[1], 0.395);
    EXPECT_DOUBLE_EQ(state.q[2], 0.049);
    EXPECT_DOUBLE_EQ(state.q[6], 0.0);
    EXPECT_DOUBLE_EQ(state.dq[0], 1.0);
    EXPECT_DOUBLE_EQ(state.dq[1], -1.0);
    EXPECT_DOUBLE_EQ(state.dq[6], 0.0);
}

TEST(BraincoHandState, RejectsMalformedOrOutOfRangeFeedback) {
    {
        msgpack::sbuffer payload;
        msgpack::pack(payload, std::map<std::string, std::array<int, 5>>{
            {"positions", {0, 0, 0, 0, 0}}});
        brainco_hand_state::JointState state;
        EXPECT_FALSE(brainco_hand_state::DecodeMotorStatus(
            payload.data(), payload.size(), state));
    }

    {
        const auto payload = PackStatus(
            {0, 0, 0, 0, 0, 1001},
            {0, 0, 0, 0, 0, 0});
        brainco_hand_state::JointState state;
        EXPECT_FALSE(brainco_hand_state::DecodeMotorStatus(
            payload.data(), payload.size(), state));
    }
}
