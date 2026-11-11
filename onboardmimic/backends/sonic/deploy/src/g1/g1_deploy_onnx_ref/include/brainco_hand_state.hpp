/**
 * @file brainco_hand_state.hpp
 * @brief Decode BrainCo MotorStatus feedback into SONIC's fixed 7-value hand state.
 *
 * Robot-side stark_node publishes six physical Revo2 motor positions in the
 * unified 0..1000 range.  The SONIC state/logger wire schema reserves seven
 * values per hand, so the physical six values are normalized to 0..1 and the
 * final compatibility slot is always zero.
 */

#pragma once

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include <msgpack.hpp>

namespace brainco_hand_state {

static constexpr std::size_t kPhysicalDof = 6;
static constexpr std::size_t kWireDof = 7;
static constexpr double kPositionScale = 1000.0;
static constexpr double kSpeedScale = 1000.0;

struct JointState {
    std::array<double, kWireDof> q{};
    std::array<double, kWireDof> dq{};
};

inline bool DecodeMotorStatus(
    const void* payload,
    std::size_t payload_size,
    JointState& output,
    std::string* error = nullptr) {
    const auto fail = [error](const std::string& message) {
        if (error != nullptr) *error = message;
        return false;
    };

    try {
        const auto object_handle = msgpack::unpack(
            static_cast<const char*>(payload), payload_size);
        const auto object = object_handle.get();
        if (object.type != msgpack::type::MAP) {
            return fail("payload is not a map");
        }

        std::map<std::string, msgpack::object> fields;
        object.convert(fields);
        if (!fields.count("positions")) {
            return fail("positions is missing");
        }

        std::vector<msgpack::object> positions;
        fields.at("positions").convert(positions);
        if (positions.size() != kPhysicalDof) {
            return fail("positions must contain exactly 6 values");
        }

        std::vector<msgpack::object> speeds;
        if (fields.count("speeds")) {
            fields.at("speeds").convert(speeds);
            if (speeds.size() != kPhysicalDof) {
                return fail("speeds must contain exactly 6 values");
            }
        }

        JointState decoded;
        for (std::size_t i = 0; i < kPhysicalDof; ++i) {
            const double position = positions[i].as<double>();
            if (!std::isfinite(position) || position < 0.0 || position > kPositionScale) {
                return fail("position is outside the unified 0..1000 range");
            }
            decoded.q[i] = position / kPositionScale;

            if (!speeds.empty()) {
                const double speed = speeds[i].as<double>();
                if (!std::isfinite(speed) || speed < -kSpeedScale || speed > kSpeedScale) {
                    return fail("speed is outside the unified -1000..1000 range");
                }
                decoded.dq[i] = speed / kSpeedScale;
            }
        }

        // q[6]/dq[6] remain zero: BrainCo has six physical motors while the
        // existing SONIC logger and RobotModel interface reserve seven slots.
        output = decoded;
        if (error != nullptr) error->clear();
        return true;
    } catch (const std::exception& exception) {
        return fail(exception.what());
    }
}

}  // namespace brainco_hand_state
