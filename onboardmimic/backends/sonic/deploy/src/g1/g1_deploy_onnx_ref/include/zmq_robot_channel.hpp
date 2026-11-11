/**
 * @file zmq_robot_channel.hpp
 * @brief ZMQ-based robot communication for SONIC (compatible with zmq_dds_bridge).
 *
 * This module allows SONIC to communicate with the robot through the same
 * zmq_dds_bridge.py that HOMIE uses. No changes needed on robot side!
 *
 * Architecture (same as HOMIE):
 *   Host (SONIC C++)                      Robot (zmq_dds_bridge.py)
 *   ┌─────────────────────┐               ┌─────────────────────────┐
 *   │  ZmqRobotChannel    │               │  zmq_dds_bridge.py      │
 *   │  - SUB ← 5560 state │ ←── ZMQ ───── │  - PUB :5560 state      │
 *   │  - PUB → 5561 cmd   │ ──── ZMQ ───→ │  - SUB :5561 cmd        │
 *   │  - UDP ← 5562 state │ ←── UDP ───── │  - UDP :5562 state      │
 *   └─────────────────────┘               └─────────────────────────┘
 *
 * Message format: msgpack (same as HOMIE's zmq_serializer.py)
 * UDP format: fixed binary (368 bytes, no serialization overhead)
 * Topics: rt/lowstate, rt/lowcmd, rt/secondary_imu,
 *         rt/{dex3,brainco}/{left,right}/{state,cmd}
 */

#pragma once

#include <zmq.hpp>
#include <msgpack.hpp>
#include <thread>
#include <atomic>
#include <mutex>
#include <functional>
#include <iostream>
#include <chrono>
#include <cstdlib>
#include <array>
#include <map>
#include <vector>
#include <string>
#include <cstring>
#include <cstdint>
#include <cerrno>

// UDP socket includes
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>

#include <unitree/idl/hg/LowCmd_.hpp>
#include <unitree/idl/hg/LowState_.hpp>
#include <unitree/idl/hg/IMUState_.hpp>
#include <unitree/idl/hg/HandCmd_.hpp>

#include "brainco_hand_state.hpp"

using namespace unitree_hg::msg::dds_;

// UDP State Protocol - must match Python side
static constexpr char UDP_STATE_MAGIC[4] = {'S', 'T', 'A', 'T'};
static constexpr int UDP_STATE_PORT = 5562;
// Format: magic(4) + tick(4) + q[35](140) + dq[35](140) + imu_quat[4](16) + imu_gyro[3](12) + imu_accel[3](12) + sec_imu_quat[4](16) + sec_imu_gyro[3](12) + sec_imu_accel[3](12)
static constexpr size_t UDP_STATE_SIZE = 4 + 4 + 35*4 + 35*4 + 4*4 + 3*4 + 3*4 + 4*4 + 3*4 + 3*4;  // 368 bytes

#pragma pack(push, 1)
struct UdpStatePacket {
    char magic[4];
    uint32_t tick;
    float q[35];
    float dq[35];
    float imu_quat[4];
    float imu_gyro[3];
    float imu_accel[3];
    float sec_imu_quat[4];
    float sec_imu_gyro[3];
    float sec_imu_accel[3];
};
#pragma pack(pop)
static_assert(sizeof(UdpStatePacket) == UDP_STATE_SIZE, "UdpStatePacket size mismatch");

// Optional third frame on SONIC LowCmd multipart messages.  Older robot
// bridges continue to consume [topic, payload] and ignore this trailer; the
// current bridge uses it to distinguish host scheduling stalls from wireless
// delivery stalls without deserializing every queued LowCmd.
#pragma pack(push, 1)
struct LowCmdTransportMetadata {
    char magic[4] = {'L', 'C', 'M', '1'};
    uint16_t version = 1;
    uint16_t flags = 0;
    uint64_t stream_id = 0;
    uint64_t sequence = 0;
    uint64_t send_steady_ns = 0;
};
#pragma pack(pop)
static_assert(sizeof(LowCmdTransportMetadata) == 32,
              "LowCmd transport metadata wire size mismatch");

// Compact LowCmd command packet.  931 bytes fits in one ordinary Ethernet
// frame (1500-byte MTU), avoiding IP fragmentation and TCP head-of-line
// blocking.  The wire is little-endian on the supported x86_64 host and
// aarch64 Unitree robot.
static constexpr char LOWCMD_UDP_MAGIC[4] = {'L', 'C', 'U', '1'};
static constexpr uint16_t LOWCMD_UDP_VERSION = 1;
static constexpr size_t LOWCMD_UDP_MOTOR_COUNT = 35;
static constexpr size_t LOWCMD_UDP_PACKET_SIZE = 931;

#pragma pack(push, 1)
struct LowCmdUdpHeader {
    char magic[4] = {'L', 'C', 'U', '1'};
    uint16_t version = LOWCMD_UDP_VERSION;
    uint16_t flags = 0;
    uint64_t stream_id = 0;
    uint64_t sequence = 0;
    uint64_t send_steady_ns = 0;
    uint8_t mode_pr = 0;
    uint8_t mode_machine = 0;
    uint8_t motor_count = LOWCMD_UDP_MOTOR_COUNT;
    uint8_t reserved = 0;
    uint32_t reserve[4] = {};
    uint32_t crc = 0;
};

struct LowCmdUdpMotor {
    uint8_t mode = 0;
    float q = 0.0f;
    float dq = 0.0f;
    float tau = 0.0f;
    float kp = 0.0f;
    float kd = 0.0f;
    uint32_t reserve = 0;
};

struct LowCmdUdpPacket {
    LowCmdUdpHeader header;
    LowCmdUdpMotor motor_cmd[LOWCMD_UDP_MOTOR_COUNT];
};
#pragma pack(pop)
static_assert(sizeof(LowCmdUdpHeader) == 56,
              "LowCmd UDP header wire size mismatch");
static_assert(sizeof(LowCmdUdpMotor) == 25,
              "LowCmd UDP motor wire size mismatch");
static_assert(sizeof(LowCmdUdpPacket) == LOWCMD_UDP_PACKET_SIZE,
              "LowCmd UDP packet must stay below Ethernet MTU");

/**
 * @brief ZMQ-based robot channel compatible with zmq_dds_bridge.py
 *
 * Provides the same interface as DDS channels but uses ZMQ for communication.
 * Fully compatible with HOMIE's zmq_dds_bridge - no changes needed on robot side.
 */
class ZmqRobotChannel {
public:
    ZmqRobotChannel()
        : running_(false),
          state_received_(false),
          commands_enabled_(true),
          lowcmd_stream_id_(
              static_cast<uint64_t>(
                  std::chrono::steady_clock::now().time_since_epoch().count()) ^
              (static_cast<uint64_t>(::getpid()) << 32)),
          lowcmd_udp_socket_(-1),
          lowcmd_udp_port_(0),
          lowcmd_udp_enabled_(false),
          lowcmd_zmq_fallback_(false),
          udp_socket_(-1),
          use_udp_(false) {}

    ~ZmqRobotChannel() {
        Stop();
    }

    /**
     * @brief Initialize ZMQ connection to robot's zmq_dds_bridge.
     * @param robot_ip Robot IP address.  If omitted, read
     *                 G1_WBC_DEFAULT_WIRELESS_ROBOT_IP from the environment.
     * @param state_port Port for receiving state (default 5560, must match bridge)
     * @param cmd_port Port for sending commands (default 5561, must match bridge)
     * @param use_udp Enable UDP for low-latency state reception (default false).
     *                When true, the ZMQ thread skips lowstate (line 316-318) and
     *                expects bridge to send UDP packets to udp_port. The default
     *                run_zmq_dds_bridge.py does NOT send UDP unless launched with
     *                --udp-target <host_ip>, so leaving this true silently breaks
     *                lowstate reception ("LowState is not available"). Default is
     *                false so we always work via ZMQ — opt in to UDP explicitly.
     * @param udp_port UDP port for state (default 5562)
     * @param lowcmd_udp_port Robot-side compact LowCmd UDP receiver.  Zero
     *                        preserves the historical ZMQ-only command path.
     */
    bool Init(const std::string& robot_ip = "",
              int state_port = 5560,
              int cmd_port = 5561,
              bool use_udp = false,
              int udp_port = UDP_STATE_PORT,
              int lowcmd_udp_port = 0) {
        robot_ip_ = robot_ip;
        if (robot_ip_.empty()) {
            const char* configured_ip = std::getenv("G1_WBC_DEFAULT_WIRELESS_ROBOT_IP");
            if (configured_ip != nullptr) {
                robot_ip_ = configured_ip;
            }
        }
        if (robot_ip_.empty()) {
            std::cerr << "[ZMQ Bridge] ERROR: robot IP is unset; pass --zmq-robot-ip or set "
                      << "G1_WBC_DEFAULT_WIRELESS_ROBOT_IP" << std::endl;
            return false;
        }
        state_port_ = state_port;
        cmd_port_ = cmd_port;
        const char* read_only = std::getenv("G1_WBC_ZMQ_READ_ONLY");
        commands_enabled_ = !(read_only && std::string(read_only) != "0");
        if (!commands_enabled_) {
            std::cout << "[ZMQ Bridge] READ-ONLY diagnostic mode: suppressing all robot commands" << std::endl;
        }
        // UDP path is permanently disabled here. The bridge (run_zmq_dds_bridge.py)
        // does not send UDP unless --udp-target is passed, so any code path that
        // depends on UDP for lowstate reception silently breaks ("LowState is not
        // available"). HOMIE (Python) never used UDP and works fine; SONIC now
        // mirrors that. Re-enable only when bridge is also configured for UDP.
        (void)use_udp;
        use_udp_ = false;
        udp_port_ = udp_port;
        lowcmd_udp_port_ = lowcmd_udp_port;
        const char* zmq_fallback_override =
            std::getenv("SONIC_LOWCMD_ZMQ_FALLBACK");
        lowcmd_zmq_fallback_ = false;
        if (zmq_fallback_override != nullptr) {
            const std::string value(zmq_fallback_override);
            if (value == "1" || value == "true" || value == "yes") {
                lowcmd_zmq_fallback_ = true;
            } else if (
                value != "0" && value != "false" && value != "no"
                && !value.empty()) {
                std::cerr
                    << "[LowCmd UDP] Invalid SONIC_LOWCMD_ZMQ_FALLBACK='"
                    << value << "' (expected 0/1, false/true, or no/yes)"
                    << std::endl;
                return false;
            }
        }

        try {
            context_ = std::make_unique<zmq::context_t>(1);

            // State subscriber (connect to robot's PUB socket)
            state_socket_ = std::make_unique<zmq::socket_t>(*context_, zmq::socket_type::sub);
            std::string state_addr = "tcp://" + robot_ip_ + ":" + std::to_string(state_port);
            state_socket_->connect(state_addr);
            // Subscribe to all state topics (same as HOMIE)
            state_socket_->set(zmq::sockopt::subscribe, "rt/lowstate");
            state_socket_->set(zmq::sockopt::subscribe, "rt/secondary_imu");
            state_socket_->set(zmq::sockopt::subscribe, "rt/dex3/left/state");
            state_socket_->set(zmq::sockopt::subscribe, "rt/dex3/right/state");
            state_socket_->set(zmq::sockopt::subscribe, "rt/brainco/left/state");
            state_socket_->set(zmq::sockopt::subscribe, "rt/brainco/right/state");
            state_socket_->set(zmq::sockopt::rcvtimeo, 100);  // 100ms timeout
            // NOTE: Do NOT use conflate=1 here! We subscribe to multiple topics
            // (rt/lowstate, rt/secondary_imu, rt/dex3/*), and conflate is socket-level,
            // not per-topic. It would discard rt/lowstate when rt/secondary_imu arrives!
            // Keep the backlog below the 500 ms LowState watchdog horizon.  A large
            // mixed-topic queue can otherwise remain continuously non-empty and make
            // ReceiverLoop spend too long draining before it publishes a callback.
            state_socket_->set(zmq::sockopt::rcvhwm, 128);
            std::cout << "[ZMQ Bridge] State SUB connected to " << state_addr << std::endl;

            // Command publisher (connect to robot's SUB socket)
            cmd_socket_ = std::make_unique<zmq::socket_t>(*context_, zmq::socket_type::pub);
            std::string cmd_addr = "tcp://" + robot_ip_ + ":" + std::to_string(cmd_port);
            // Socket options affect only subsequent connections.  Set them
            // before connect so a congested wireless TCP pipe cannot build a
            // large stale command backlog.
            cmd_socket_->set(zmq::sockopt::sndhwm, 10);
            cmd_socket_->set(zmq::sockopt::linger, 0);
            cmd_socket_->set(zmq::sockopt::immediate, 1);
            cmd_socket_->connect(cmd_addr);
            std::cout << "[ZMQ Bridge] Command PUB connected to " << cmd_addr << std::endl;

            if (lowcmd_udp_port_ > 0) {
                lowcmd_udp_socket_ = socket(AF_INET, SOCK_DGRAM, 0);
                if (lowcmd_udp_socket_ < 0) {
                    std::cerr
                        << "[LowCmd UDP] Failed to create command socket: "
                        << std::strerror(errno) << std::endl;
                    return false;
                }
                int flags = fcntl(lowcmd_udp_socket_, F_GETFL, 0);
                if (flags < 0
                    || fcntl(
                           lowcmd_udp_socket_,
                           F_SETFL,
                           flags | O_NONBLOCK) < 0) {
                    std::cerr
                        << "[LowCmd UDP] Failed to make socket non-blocking: "
                        << std::strerror(errno) << std::endl;
                    return false;
                }
                int send_buffer_bytes = 64 * 1024;
                (void)setsockopt(
                    lowcmd_udp_socket_,
                    SOL_SOCKET,
                    SO_SNDBUF,
                    &send_buffer_bytes,
                    sizeof(send_buffer_bytes));
                // LowCmd shares the robot Wi-Fi with two high-rate camera
                // streams and state telemetry.  Mark it EF/WMM-voice and give
                // it the highest unprivileged Linux socket priority so command
                // datagrams do not sit behind bulk camera/state traffic.
                // The safety watchdog remains authoritative if the network
                // still cannot deliver a fresh command within its deadline.
                int lowcmd_ip_tos = 46 << 2;  // DSCP EF, ECN=0.
                if (setsockopt(
                        lowcmd_udp_socket_,
                        IPPROTO_IP,
                        IP_TOS,
                        &lowcmd_ip_tos,
                        sizeof(lowcmd_ip_tos)) != 0) {
                    std::cerr
                        << "[LowCmd UDP] WARNING: failed to set DSCP EF: "
                        << std::strerror(errno) << std::endl;
                }
                int lowcmd_socket_priority = 6;
                if (setsockopt(
                        lowcmd_udp_socket_,
                        SOL_SOCKET,
                        SO_PRIORITY,
                        &lowcmd_socket_priority,
                        sizeof(lowcmd_socket_priority)) != 0) {
                    std::cerr
                        << "[LowCmd UDP] WARNING: failed to set SO_PRIORITY=6: "
                        << std::strerror(errno) << std::endl;
                }

                std::memset(
                    &lowcmd_udp_target_, 0, sizeof(lowcmd_udp_target_));
                lowcmd_udp_target_.sin_family = AF_INET;
                lowcmd_udp_target_.sin_port = htons(lowcmd_udp_port_);
                if (inet_pton(
                        AF_INET,
                        robot_ip_.c_str(),
                        &lowcmd_udp_target_.sin_addr) != 1) {
                    std::cerr
                        << "[LowCmd UDP] Robot endpoint must be an IPv4 "
                        << "literal (got: " << robot_ip_ << ")" << std::endl;
                    return false;
                }
                lowcmd_udp_enabled_ = true;
                std::cout
                    << "[LowCmd UDP] Primary command path -> udp://"
                    << robot_ip_ << ":" << lowcmd_udp_port_
                    << " (" << sizeof(LowCmdUdpPacket)
                    << " bytes/datagram, ZMQ compatibility copy="
                    << (lowcmd_zmq_fallback_ ? "on" : "off")
                    << ", DSCP=EF, SO_PRIORITY=6)"
                    << std::endl;
            }

            // Wait for ZMQ subscription to propagate (important!)
            std::cout << "[ZMQ Bridge] Waiting for subscription propagation..." << std::endl;
            std::this_thread::sleep_for(std::chrono::milliseconds(2000));

            // Start receiver thread (ZMQ for commands, still needed for IMU if not in UDP)
            running_ = true;
            receiver_thread_ = std::thread(&ZmqRobotChannel::ReceiverLoop, this);

            // Initialize UDP socket for low-latency state reception
            if (use_udp_) {
                udp_socket_ = socket(AF_INET, SOCK_DGRAM, 0);
                if (udp_socket_ < 0) {
                    std::cerr << "[ZMQ Bridge] Failed to create UDP socket" << std::endl;
                    use_udp_ = false;
                } else {
                    // Set non-blocking
                    int flags = fcntl(udp_socket_, F_GETFL, 0);
                    fcntl(udp_socket_, F_SETFL, flags | O_NONBLOCK);

                    // Bind to local port to receive state from robot
                    struct sockaddr_in local_addr;
                    memset(&local_addr, 0, sizeof(local_addr));
                    local_addr.sin_family = AF_INET;
                    local_addr.sin_addr.s_addr = INADDR_ANY;
                    local_addr.sin_port = htons(udp_port_);

                    if (bind(udp_socket_, (struct sockaddr*)&local_addr, sizeof(local_addr)) < 0) {
                        std::cerr << "[ZMQ Bridge] Failed to bind UDP socket to port " << udp_port_ << std::endl;
                        close(udp_socket_);
                        udp_socket_ = -1;
                        use_udp_ = false;
                    } else {
                        std::cout << "[ZMQ Bridge] UDP state receiver bound to port " << udp_port_ << std::endl;
                        // Start UDP receiver thread
                        udp_receiver_thread_ = std::thread(&ZmqRobotChannel::UdpReceiverLoop, this);
                    }
                }
            }

            std::cout << "[ZMQ Bridge] Robot channel initialized (compatible with zmq_dds_bridge)" << std::endl;
            if (use_udp_) {
                std::cout << "[ZMQ Bridge] Using UDP for low-latency state reception (port " << udp_port_ << ")" << std::endl;
            }
            return true;

        } catch (const zmq::error_t& e) {
            std::cerr << "[ZMQ Bridge] Failed to initialize: " << e.what() << std::endl;
            return false;
        }
    }

    void Stop() {
        running_ = false;
        if (receiver_thread_.joinable()) {
            receiver_thread_.join();
        }
        if (udp_receiver_thread_.joinable()) {
            udp_receiver_thread_.join();
        }
        if (udp_socket_ >= 0) {
            close(udp_socket_);
            udp_socket_ = -1;
        }
        if (lowcmd_udp_socket_ >= 0) {
            close(lowcmd_udp_socket_);
            lowcmd_udp_socket_ = -1;
        }
        lowcmd_udp_enabled_ = false;
        state_socket_.reset();
        cmd_socket_.reset();
        context_.reset();
    }

    /**
     * @brief Set callback for LowState messages (same signature as DDS handler).
     */
    void SetLowStateCallback(std::function<void(const void*)> callback) {
        std::lock_guard<std::mutex> lock(callback_mutex_);
        low_state_callback_ = callback;
    }

    /**
     * @brief Set callback for secondary IMU messages.
     */
    void SetImuCallback(std::function<void(const void*)> callback) {
        std::lock_guard<std::mutex> lock(callback_mutex_);
        imu_callback_ = callback;
    }

    /**
     * @brief Copy the latest physical BrainCo feedback into SONIC's 7-value schema.
     *
     * The first six values are normalized physical motor positions/speeds; the
     * seventh value is the existing compatibility padding.  Stale feedback is
     * rejected so callers cannot silently treat an old command/pose as current.
     */
    bool GetBraincoJointState(
        bool is_left,
        std::array<double, brainco_hand_state::kWireDof>& q,
        std::array<double, brainco_hand_state::kWireDof>& dq,
        double max_age_ms = 500.0) const {
        q.fill(0.0);
        dq.fill(0.0);

        const auto& received_ns = is_left
            ? last_brainco_left_rx_ns_
            : last_brainco_right_rx_ns_;
        const int64_t timestamp_ns = received_ns.load(std::memory_order_acquire);
        if (timestamp_ns == 0) return false;
        if (max_age_ms > 0.0) {
            const double age_ms = static_cast<double>(SteadyNowNs() - timestamp_ns) / 1e6;
            if (age_ms < 0.0 || age_ms > max_age_ms) return false;
        }

        std::lock_guard<std::mutex> lock(brainco_state_mutex_);
        const auto& state = is_left ? brainco_left_state_ : brainco_right_state_;
        q = state.q;
        dq = state.dq;
        return true;
    }

    /**
     * @brief Publish LowCmd to the robot.
     *
     * A configured compact UDP socket is the authoritative real-robot path.
     * The historical ZMQ multipart remains available only as an explicit
     * SONIC_LOWCMD_ZMQ_FALLBACK=1 migration aid.  Sending both at 500 Hz can
     * congest the shared robot Wi-Fi and starve LowState, so UDP mode defaults
     * to one LowCmd transport.  Simulation leaves the UDP port at zero and
     * remains ZMQ-only.
     */
    bool PublishLowCmd(const LowCmd_& cmd) {
        if (!commands_enabled_) return true;
        const uint64_t attempt_ns = static_cast<uint64_t>(SteadyNowNs());
        const uint64_t previous_attempt_ns = last_lowcmd_attempt_ns_;
        last_lowcmd_attempt_ns_ = attempt_ns;
        if (previous_attempt_ns != 0 && attempt_ns >= previous_attempt_ns) {
            lowcmd_interval_max_ns_ =
                std::max(lowcmd_interval_max_ns_, attempt_ns - previous_attempt_ns);
        }
        ++lowcmd_attempts_;

        if (!cmd_socket_ && !lowcmd_udp_enabled_) {
            RecordLowCmdSendResult(false, attempt_ns, 0, 0);
            return false;
        }

        LowCmdTransportMetadata metadata;
        metadata.stream_id = lowcmd_stream_id_;
        metadata.sequence = ++lowcmd_sequence_;
        metadata.send_steady_ns = attempt_ns;

        bool udp_success = false;
        if (lowcmd_udp_enabled_) {
            LowCmdUdpPacket packet;
            packet.header.stream_id = metadata.stream_id;
            packet.header.sequence = metadata.sequence;
            packet.header.send_steady_ns = metadata.send_steady_ns;
            packet.header.mode_pr = static_cast<uint8_t>(cmd.mode_pr());
            packet.header.mode_machine =
                static_cast<uint8_t>(cmd.mode_machine());
            for (size_t i = 0; i < LOWCMD_UDP_MOTOR_COUNT; ++i) {
                const auto& source = cmd.motor_cmd()[i];
                auto& target = packet.motor_cmd[i];
                target.mode = static_cast<uint8_t>(source.mode());
                target.q = static_cast<float>(source.q());
                target.dq = static_cast<float>(source.dq());
                target.tau = static_cast<float>(source.tau());
                target.kp = static_cast<float>(source.kp());
                target.kd = static_cast<float>(source.kd());
                target.reserve =
                    static_cast<uint32_t>(source.reserve());
            }
            for (size_t i = 0; i < 4; ++i) {
                packet.header.reserve[i] =
                    static_cast<uint32_t>(cmd.reserve()[i]);
            }
            packet.header.crc = static_cast<uint32_t>(cmd.crc());

            const ssize_t sent = sendto(
                lowcmd_udp_socket_,
                &packet,
                sizeof(packet),
                MSG_DONTWAIT | MSG_NOSIGNAL,
                reinterpret_cast<const struct sockaddr*>(
                    &lowcmd_udp_target_),
                sizeof(lowcmd_udp_target_));
            udp_success =
                sent == static_cast<ssize_t>(sizeof(packet));
            if (!udp_success) {
                const uint64_t failures = ++lowcmd_udp_send_failures_;
                if (failures <= 5 || failures % 500 == 0) {
                    std::cerr
                        << "[LowCmd UDP] WARNING: command datagram send "
                        << "failed (sent=" << sent
                        << ", errno=" << errno << ": "
                        << std::strerror(errno) << ", count="
                        << failures << ")" << std::endl;
                }
            }
        }

        // ZMQ socket is shared with PublishDex3Cmd / PublishBraincoCmd.  This
        // mutex protects the compatibility copy and hand topics; the primary
        // UDP send above never waits on hand traffic.
        const uint64_t lock_start_ns = static_cast<uint64_t>(SteadyNowNs());
        uint64_t lock_wait_ns = 0;
        bool zmq_success = false;
        try {
            if (
                cmd_socket_
                && (!lowcmd_udp_enabled_ || lowcmd_zmq_fallback_)
            ) {
                std::unique_lock<std::mutex> cmd_lock(cmd_socket_mutex_);
                const uint64_t lock_acquired_ns =
                    static_cast<uint64_t>(SteadyNowNs());
                lock_wait_ns = lock_acquired_ns - lock_start_ns;

                // Serialize to msgpack dict (matching Python's low_cmd_to_dict).
                msgpack::sbuffer sbuf;
                msgpack::packer<msgpack::sbuffer> pk(&sbuf);
                pk.pack_map(5);
                pk.pack("mode_pr");
                pk.pack(static_cast<int>(cmd.mode_pr()));
                pk.pack("mode_machine");
                pk.pack(static_cast<int>(cmd.mode_machine()));
                pk.pack("motor_cmd");
                pk.pack_array(35);
                for (int i = 0; i < 35; i++) {
                    pk.pack_map(7);
                    pk.pack("mode");
                    pk.pack(static_cast<int>(cmd.motor_cmd()[i].mode()));
                    pk.pack("q");
                    pk.pack(static_cast<double>(cmd.motor_cmd()[i].q()));
                    pk.pack("dq");
                    pk.pack(static_cast<double>(cmd.motor_cmd()[i].dq()));
                    pk.pack("tau");
                    pk.pack(static_cast<double>(cmd.motor_cmd()[i].tau()));
                    pk.pack("kp");
                    pk.pack(static_cast<double>(cmd.motor_cmd()[i].kp()));
                    pk.pack("kd");
                    pk.pack(static_cast<double>(cmd.motor_cmd()[i].kd()));
                    pk.pack("reserve");
                    pk.pack(
                        static_cast<int>(
                            cmd.motor_cmd()[i].reserve()));
                }
                pk.pack("reserve");
                pk.pack_array(4);
                for (int i = 0; i < 4; i++) {
                    pk.pack(static_cast<int>(cmd.reserve()[i]));
                }
                pk.pack("crc");
                pk.pack(static_cast<int>(cmd.crc()));

                std::string topic = "rt/lowcmd";
                zmq::message_t topic_msg(topic.data(), topic.size());
                zmq::message_t data_msg(sbuf.data(), sbuf.size());
                zmq::message_t metadata_msg(&metadata, sizeof(metadata));
                auto rc1 = cmd_socket_->send(
                    topic_msg,
                    zmq::send_flags::sndmore
                        | zmq::send_flags::dontwait);
                auto rc2 = cmd_socket_->send(
                    data_msg,
                    zmq::send_flags::sndmore
                        | zmq::send_flags::dontwait);
                auto rc3 = cmd_socket_->send(
                    metadata_msg, zmq::send_flags::dontwait);
                zmq_success =
                    rc1.has_value()
                    && rc2.has_value()
                    && rc3.has_value();
            }
            const uint64_t completed_ns = static_cast<uint64_t>(SteadyNowNs());
            const bool success =
                lowcmd_udp_enabled_ ? udp_success : zmq_success;
            RecordLowCmdSendResult(
                success,
                completed_ns,
                lock_wait_ns,
                completed_ns - attempt_ns);
            if (!success) {
                return false;
            }

            cmd_count_++;

            return true;

        } catch (const std::exception& e) {
            const uint64_t completed_ns = static_cast<uint64_t>(SteadyNowNs());
            if (lowcmd_udp_enabled_ && udp_success) {
                RecordLowCmdSendResult(
                    true,
                    completed_ns,
                    lock_wait_ns,
                    completed_ns - attempt_ns);
                const uint64_t failures = ++lowcmd_zmq_fallback_failures_;
                if (failures <= 5 || failures % 500 == 0) {
                    std::cerr
                        << "[LowCmd TX] ZMQ compatibility copy failed after "
                        << "the UDP datagram was sent: " << e.what()
                        << " (count=" << failures << ")" << std::endl;
                }
                ++cmd_count_;
                return true;
            }
            RecordLowCmdSendResult(
                false,
                completed_ns,
                lock_wait_ns,
                completed_ns - attempt_ns);
            std::cerr
                << "[LowCmd TX] Failed to publish command: "
                << e.what() << std::endl;
            return false;
        }
    }

    /**
     * @brief Publish Dex3 HandCmd to robot via ZMQ (compatible with zmq_dds_bridge.py).
     *
     * Topic: rt/dex3/left/cmd or rt/dex3/right/cmd
     * Format matches zmq_serializer.py:hand_cmd_to_dict — robot side bridge
     * reads {motor_cmd[7], reserve[4]} where each motor_cmd is
     * {mode,q,dq,tau,kp,kd,reserve}.
     */
    bool PublishHandCmd(bool is_left, const HandCmd_& cmd) {
        if (!commands_enabled_) return true;
        if (!cmd_socket_) return false;

        std::lock_guard<std::mutex> _cmd_lock(cmd_socket_mutex_);
        try {
            msgpack::sbuffer sbuf;
            msgpack::packer<msgpack::sbuffer> pk(&sbuf);

            // Top-level map: {motor_cmd, reserve}
            pk.pack_map(2);

            pk.pack("motor_cmd");
            pk.pack_array(7);
            for (int i = 0; i < 7; i++) {
                pk.pack_map(7);
                pk.pack("mode");    pk.pack(static_cast<int>(cmd.motor_cmd()[i].mode()));
                pk.pack("q");       pk.pack(static_cast<double>(cmd.motor_cmd()[i].q()));
                pk.pack("dq");      pk.pack(static_cast<double>(cmd.motor_cmd()[i].dq()));
                pk.pack("tau");     pk.pack(static_cast<double>(cmd.motor_cmd()[i].tau()));
                pk.pack("kp");      pk.pack(static_cast<double>(cmd.motor_cmd()[i].kp()));
                pk.pack("kd");      pk.pack(static_cast<double>(cmd.motor_cmd()[i].kd()));
                pk.pack("reserve"); pk.pack(static_cast<int>(cmd.motor_cmd()[i].reserve()));
            }

            pk.pack("reserve");
            pk.pack_array(4);
            for (int i = 0; i < 4; i++) {
                pk.pack(static_cast<int>(cmd.reserve()[i]));
            }

            std::string topic = is_left ? "rt/dex3/left/cmd" : "rt/dex3/right/cmd";
            zmq::message_t topic_msg(topic.data(), topic.size());
            zmq::message_t data_msg(sbuf.data(), sbuf.size());

            auto rc1 = cmd_socket_->send(topic_msg, zmq::send_flags::sndmore | zmq::send_flags::dontwait);
            auto rc2 = cmd_socket_->send(data_msg, zmq::send_flags::dontwait);
            if (!rc1 || !rc2) return false;  // would block; drop

            cmd_count_++;
            return true;

        } catch (const std::exception& e) {
            std::cerr << "[ZMQ Bridge] Failed to publish HandCmd: " << e.what() << std::endl;
            return false;
        }
    }

    /**
     * @brief Publish a BrainCo Revo2 6-DOF staged command via ZMQ.
     *
     * Topic: rt/brainco/{left,right}/cmd
     * Format matches zmq_serializer.py:joint_cmd_to_dict
     *   { "position": [6 floats in 0~1] }
     * Robot side bridge forwards to /joint_commands_{left,right};
     * stark_node drives USB-485.
     */
    bool PublishBraincoCmd(bool is_left, const std::array<double, 6>& cmd) {
        if (!commands_enabled_) return true;
        if (!cmd_socket_) return false;
        std::lock_guard<std::mutex> _cmd_lock(cmd_socket_mutex_);
        try {
            msgpack::sbuffer sbuf;
            msgpack::packer<msgpack::sbuffer> pk(&sbuf);
            pk.pack_map(1);
            pk.pack("position");
            pk.pack_array(6);
            for (double v : cmd) pk.pack(v);

            std::string topic = is_left ? "rt/brainco/left/cmd" : "rt/brainco/right/cmd";
            zmq::message_t topic_msg(topic.data(), topic.size());
            zmq::message_t data_msg(sbuf.data(), sbuf.size());
            auto rc1 = cmd_socket_->send(topic_msg, zmq::send_flags::sndmore | zmq::send_flags::dontwait);
            auto rc2 = cmd_socket_->send(data_msg, zmq::send_flags::dontwait);
            if (!rc1 || !rc2) return false;
            cmd_count_++;
            return true;
        } catch (const std::exception& e) {
            std::cerr << "[ZMQ Bridge] Failed to publish BraincoCmd: " << e.what() << std::endl;
            return false;
        }
    }

    bool HasReceivedState() const { return state_received_; }
    uint64_t GetStateCount() const { return state_count_; }
    uint64_t GetCmdCount() const { return cmd_count_; }
    double GetLastLowStateAgeMs() const {
        const int64_t last_ns = last_lowstate_rx_ns_.load();
        if (last_ns == 0) return -1.0;
        return static_cast<double>(SteadyNowNs() - last_ns) / 1e6;
    }

private:
    void ReceiverLoop() {
        std::cout << "[ZMQ Bridge] Receiver thread started" << std::endl;

        // Track latest messages for each topic (drain mode)
        LowState_ latest_lowstate;
        IMUState_ latest_imu;
        bool has_lowstate = false;
        bool has_imu = false;
        uint64_t drain_count = 0;

        while (running_) {
            try {
                // Drain a bounded batch and then publish the latest state in that
                // batch.  This loop used to drain until the socket was empty.  With
                // continuous LowState + IMU traffic the socket may never become empty,
                // starving the callback long enough to trip the 500 ms safety watchdog
                // even though fresh packets are still arriving.
                has_lowstate = false;
                has_imu = false;

                static constexpr size_t kMaxMessagesPerBatch = 64;
                for (size_t batch_index = 0;
                     running_ && batch_index < kMaxMessagesPerBatch;
                     ++batch_index) {
                    zmq::message_t topic_msg, data_msg;

                    // Try non-blocking receive
                    auto result = state_socket_->recv(topic_msg, zmq::recv_flags::dontwait);
                    if (!result) break;  // No more messages

                    result = state_socket_->recv(data_msg, zmq::recv_flags::dontwait);
                    if (!result) break;

                    std::string topic(static_cast<char*>(topic_msg.data()), topic_msg.size());

                    if (topic == "rt/lowstate") {
                        // When UDP is enabled, skip ZMQ lowstate - UDP handles it
                        if (use_udp_) {
                            continue;  // Skip, UDP thread handles lowstate
                        }
                        if (DeserializeLowState(data_msg)) {
                            latest_lowstate = current_low_state_;
                            has_lowstate = true;
                            drain_count++;
                            last_lowstate_rx_ns_ = SteadyNowNs();
                        }
                    } else if (topic == "rt/secondary_imu") {
                        // When UDP is enabled, skip ZMQ secondary_imu - UDP handles it
                        if (use_udp_) {
                            continue;
                        }
                        if (DeserializeImu(data_msg)) {
                            latest_imu = current_imu_;
                            has_imu = true;
                        }
                    } else if (topic == "rt/brainco/left/state") {
                        DeserializeBraincoState(data_msg, true);
                    } else if (topic == "rt/brainco/right/state") {
                        DeserializeBraincoState(data_msg, false);
                    }
                    // Dex3 state continues to use its legacy DDS HandState path.
                }

                // Process the latest messages
                if (has_lowstate) {
                    state_received_ = true;
                    state_count_++;

                    std::lock_guard<std::mutex> lock(callback_mutex_);
                    if (low_state_callback_) {
                        low_state_callback_(&latest_lowstate);
                    }
                }

                if (has_imu) {
                    std::lock_guard<std::mutex> lock(callback_mutex_);
                    if (imu_callback_) {
                        imu_callback_(&latest_imu);
                    }
                }

                // Stats printing disabled (was spammy: every 500 frames at ~500Hz = 1Hz)
                // Re-enable with ZMQ_BRIDGE_VERBOSE=1 if needed for debugging.
                const uint64_t state_count = state_count_.load();
                if (state_count > 0 && state_count % 500 == 0
                    && state_count != last_verbose_state_count_.load()
                    && std::getenv("ZMQ_BRIDGE_VERBOSE")) {
                    last_verbose_state_count_ = state_count;
                    std::cout << "[ZMQ Bridge] State rx: " << state_count
                              << ", drained: " << drain_count << std::endl;
                }

                // Small sleep to avoid busy loop when no messages
                if (!has_lowstate && !has_imu) {
                    std::this_thread::sleep_for(std::chrono::microseconds(100));
                }

            } catch (const zmq::error_t& e) {
                if (running_ && e.num() != EAGAIN) {
                    std::cerr << "[ZMQ Bridge] Receive error: " << e.what() << std::endl;
                }
            }
        }

        std::cout << "[ZMQ Bridge] Receiver thread stopped" << std::endl;
    }

    /**
     * @brief UDP receiver loop for low-latency state reception.
     * Receives fixed-size binary packets (368 bytes) with no serialization overhead.
     */
    void UdpReceiverLoop() {
        std::cout << "[ZMQ Bridge] UDP receiver thread started" << std::endl;

        UdpStatePacket packet;
        struct sockaddr_in sender_addr;
        socklen_t sender_len = sizeof(sender_addr);

        while (running_) {
            ssize_t received = recvfrom(udp_socket_, &packet, sizeof(packet), 0,
                                        (struct sockaddr*)&sender_addr, &sender_len);

            if (received == sizeof(UdpStatePacket)) {
                // Verify magic
                if (memcmp(packet.magic, UDP_STATE_MAGIC, 4) != 0) {
                    continue;  // Invalid packet
                }

                // Unpack into LowState_
                {
                    std::lock_guard<std::mutex> lock(udp_state_mutex_);

                    udp_low_state_.tick() = packet.tick;

                    // Motor states (q, dq)
                    for (int i = 0; i < 35; i++) {
                        udp_low_state_.motor_state()[i].q() = packet.q[i];
                        udp_low_state_.motor_state()[i].dq() = packet.dq[i];
                    }

                    // Primary IMU
                    for (int i = 0; i < 4; i++) {
                        udp_low_state_.imu_state().quaternion()[i] = packet.imu_quat[i];
                    }
                    for (int i = 0; i < 3; i++) {
                        udp_low_state_.imu_state().gyroscope()[i] = packet.imu_gyro[i];
                        udp_low_state_.imu_state().accelerometer()[i] = packet.imu_accel[i];
                    }

                    // Secondary IMU
                    for (int i = 0; i < 4; i++) {
                        udp_sec_imu_.quaternion()[i] = packet.sec_imu_quat[i];
                    }
                    for (int i = 0; i < 3; i++) {
                        udp_sec_imu_.gyroscope()[i] = packet.sec_imu_gyro[i];
                        udp_sec_imu_.accelerometer()[i] = packet.sec_imu_accel[i];
                    }
                }

                state_received_ = true;
                udp_state_count_++;

                // Note: Observation logging is now done in LowStateHandler (shared by DDS and UDP modes)

                // Call the callback with UDP state
                {
                    std::lock_guard<std::mutex> lock(callback_mutex_);
                    if (low_state_callback_) {
                        std::lock_guard<std::mutex> state_lock(udp_state_mutex_);
                        low_state_callback_(&udp_low_state_);
                    }
                    if (imu_callback_) {
                        std::lock_guard<std::mutex> state_lock(udp_state_mutex_);
                        imu_callback_(&udp_sec_imu_);
                    }
                }

            } else if (received < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                // No data available, sleep a bit
                std::this_thread::sleep_for(std::chrono::microseconds(100));
            } else if (received > 0) {
                // Wrong size packet
                static int bad_packet_count = 0;
                if (++bad_packet_count <= 5) {
                    std::cerr << "[ZMQ Bridge] UDP bad packet size: " << received
                              << " (expected " << sizeof(UdpStatePacket) << ")" << std::endl;
                }
            }
        }

        std::cout << "[ZMQ Bridge] UDP receiver thread stopped" << std::endl;
    }

    bool DeserializeLowState(const zmq::message_t& msg) {
        try {
            msgpack::object_handle oh = msgpack::unpack(
                static_cast<const char*>(msg.data()), msg.size());
            msgpack::object obj = oh.get();

            if (obj.type != msgpack::type::MAP) return false;

            std::map<std::string, msgpack::object> map;
            obj.convert(map);

            // Parse basic fields
            if (map.count("version")) {
                std::vector<msgpack::object> version;
                map["version"].convert(version);
                for (size_t i = 0; i < std::min(version.size(), (size_t)2); i++) {
                    current_low_state_.version()[i] = version[i].as<uint32_t>();
                }
            }
            if (map.count("mode_pr")) current_low_state_.mode_pr() = map["mode_pr"].as<uint8_t>();
            if (map.count("mode_machine")) current_low_state_.mode_machine() = map["mode_machine"].as<uint8_t>();
            if (map.count("tick")) current_low_state_.tick() = map["tick"].as<uint32_t>();
            if (map.count("crc")) current_low_state_.crc() = map["crc"].as<uint32_t>();

            // Parse IMU state
            if (map.count("imu_state")) {
                std::map<std::string, msgpack::object> imu_map;
                map["imu_state"].convert(imu_map);

                if (imu_map.count("quaternion")) {
                    std::vector<double> q;
                    imu_map["quaternion"].convert(q);
                    for (size_t i = 0; i < std::min(q.size(), (size_t)4); i++) {
                        current_low_state_.imu_state().quaternion()[i] = static_cast<float>(q[i]);
                    }
                }
                if (imu_map.count("gyroscope")) {
                    std::vector<double> g;
                    imu_map["gyroscope"].convert(g);
                    for (size_t i = 0; i < std::min(g.size(), (size_t)3); i++) {
                        current_low_state_.imu_state().gyroscope()[i] = static_cast<float>(g[i]);
                    }
                }
                if (imu_map.count("accelerometer")) {
                    std::vector<double> a;
                    imu_map["accelerometer"].convert(a);
                    for (size_t i = 0; i < std::min(a.size(), (size_t)3); i++) {
                        current_low_state_.imu_state().accelerometer()[i] = static_cast<float>(a[i]);
                    }
                }
                if (imu_map.count("rpy")) {
                    std::vector<double> r;
                    imu_map["rpy"].convert(r);
                    for (size_t i = 0; i < std::min(r.size(), (size_t)3); i++) {
                        current_low_state_.imu_state().rpy()[i] = static_cast<float>(r[i]);
                    }
                }
            }

            // Parse motor states
            if (map.count("motor_state")) {
                std::vector<msgpack::object> motors;
                map["motor_state"].convert(motors);

                for (size_t i = 0; i < std::min(motors.size(), (size_t)35); i++) {
                    std::map<std::string, msgpack::object> m;
                    motors[i].convert(m);

                    if (m.count("mode")) current_low_state_.motor_state()[i].mode() = m["mode"].as<uint8_t>();
                    if (m.count("q")) current_low_state_.motor_state()[i].q() = m["q"].as<float>();
                    if (m.count("dq")) current_low_state_.motor_state()[i].dq() = m["dq"].as<float>();
                    if (m.count("ddq")) current_low_state_.motor_state()[i].ddq() = m["ddq"].as<float>();
                    if (m.count("tau_est")) current_low_state_.motor_state()[i].tau_est() = m["tau_est"].as<float>();
                    if (m.count("temperature")) {
                        std::vector<msgpack::object> temperature;
                        m["temperature"].convert(temperature);
                        for (size_t j = 0; j < std::min(temperature.size(), (size_t)2); j++) {
                            current_low_state_.motor_state()[i].temperature()[j] =
                                temperature[j].as<int16_t>();
                        }
                    }
                    if (m.count("vol")) current_low_state_.motor_state()[i].vol() = m["vol"].as<float>();
                    if (m.count("sensor")) {
                        std::vector<msgpack::object> sensor;
                        m["sensor"].convert(sensor);
                        for (size_t j = 0; j < std::min(sensor.size(), (size_t)2); j++) {
                            current_low_state_.motor_state()[i].sensor()[j] = sensor[j].as<uint32_t>();
                        }
                    }
                    if (m.count("motorstate")) {
                        current_low_state_.motor_state()[i].motorstate() = m["motorstate"].as<uint32_t>();
                    }
                    if (m.count("reserve")) {
                        std::vector<msgpack::object> reserve;
                        m["reserve"].convert(reserve);
                        for (size_t j = 0; j < std::min(reserve.size(), (size_t)4); j++) {
                            current_low_state_.motor_state()[i].reserve()[j] = reserve[j].as<uint32_t>();
                        }
                    }
                }
            }

            if (map.count("wireless_remote")) {
                std::vector<msgpack::object> remote;
                map["wireless_remote"].convert(remote);
                for (size_t i = 0; i < std::min(remote.size(), (size_t)40); i++) {
                    current_low_state_.wireless_remote()[i] = remote[i].as<uint8_t>();
                }
            }
            if (map.count("reserve")) {
                std::vector<msgpack::object> reserve;
                map["reserve"].convert(reserve);
                for (size_t i = 0; i < std::min(reserve.size(), (size_t)4); i++) {
                    current_low_state_.reserve()[i] = reserve[i].as<uint32_t>();
                }
            }

            return true;

        } catch (const std::exception& e) {
            std::cerr << "[ZMQ Bridge] Failed to deserialize LowState: " << e.what() << std::endl;
            return false;
        }
    }

    bool DeserializeImu(const zmq::message_t& msg) {
        try {
            msgpack::object_handle oh = msgpack::unpack(
                static_cast<const char*>(msg.data()), msg.size());
            msgpack::object obj = oh.get();

            if (obj.type != msgpack::type::MAP) return false;

            std::map<std::string, msgpack::object> map;
            obj.convert(map);

            if (map.count("quaternion")) {
                std::vector<double> q;
                map["quaternion"].convert(q);
                for (size_t i = 0; i < std::min(q.size(), (size_t)4); i++) {
                    current_imu_.quaternion()[i] = static_cast<float>(q[i]);
                }
            }
            if (map.count("gyroscope")) {
                std::vector<double> g;
                map["gyroscope"].convert(g);
                for (size_t i = 0; i < std::min(g.size(), (size_t)3); i++) {
                    current_imu_.gyroscope()[i] = static_cast<float>(g[i]);
                }
            }
            if (map.count("accelerometer")) {
                std::vector<double> a;
                map["accelerometer"].convert(a);
                for (size_t i = 0; i < std::min(a.size(), (size_t)3); i++) {
                    current_imu_.accelerometer()[i] = static_cast<float>(a[i]);
                }
            }
            if (map.count("rpy")) {
                std::vector<double> r;
                map["rpy"].convert(r);
                for (size_t i = 0; i < std::min(r.size(), (size_t)3); i++) {
                    current_imu_.rpy()[i] = static_cast<float>(r[i]);
                }
            }

            return true;

        } catch (const std::exception& e) {
            std::cerr << "[ZMQ Bridge] Failed to deserialize IMU: " << e.what() << std::endl;
            return false;
        }
    }

    bool DeserializeBraincoState(const zmq::message_t& msg, bool is_left) {
        brainco_hand_state::JointState decoded;
        std::string error;
        if (!brainco_hand_state::DecodeMotorStatus(
                msg.data(), msg.size(), decoded, &error)) {
            const uint64_t failures = ++brainco_decode_failures_;
            if (failures <= 5) {
                std::cerr << "[ZMQ Bridge] Failed to deserialize BrainCo "
                          << (is_left ? "LEFT" : "RIGHT")
                          << " state: " << error << std::endl;
            }
            return false;
        }

        {
            std::lock_guard<std::mutex> lock(brainco_state_mutex_);
            if (is_left) {
                brainco_left_state_ = decoded;
            } else {
                brainco_right_state_ = decoded;
            }
        }
        auto& received_ns = is_left
            ? last_brainco_left_rx_ns_
            : last_brainco_right_rx_ns_;
        received_ns.store(SteadyNowNs(), std::memory_order_release);
        return true;
    }

private:
    void RecordLowCmdSendResult(
        bool success,
        uint64_t now_ns,
        uint64_t lock_wait_ns,
        uint64_t send_work_ns) {
        lowcmd_lock_wait_max_ns_ =
            std::max(lowcmd_lock_wait_max_ns_, lock_wait_ns);
        lowcmd_send_work_max_ns_ =
            std::max(lowcmd_send_work_max_ns_, send_work_ns);

        if (success) {
            ++lowcmd_send_successes_;
            if (lowcmd_consecutive_failures_ != 0) {
                std::cerr
                    << "[LowCmd TX] Send recovered after "
                    << lowcmd_consecutive_failures_ << " failed attempt(s)"
                    << std::endl;
                lowcmd_consecutive_failures_ = 0;
            }
        } else {
            ++lowcmd_send_failures_;
            ++lowcmd_consecutive_failures_;
            if (lowcmd_consecutive_failures_ == 1) {
                std::cerr
                    << "[LowCmd TX] WARNING: non-blocking send failed; "
                    << "command frame dropped" << std::endl;
            }
        }

        constexpr uint64_t kReportIntervalNs = 5'000'000'000ULL;
        if (lowcmd_last_report_ns_ == 0) {
            lowcmd_last_report_ns_ = now_ns;
        }
        if (now_ns - lowcmd_last_report_ns_ < kReportIntervalNs) {
            return;
        }

        const uint64_t interval_attempts =
            lowcmd_attempts_ - lowcmd_reported_attempts_;
        const uint64_t interval_successes =
            lowcmd_send_successes_ - lowcmd_reported_successes_;
        const uint64_t interval_failures =
            lowcmd_send_failures_ - lowcmd_reported_failures_;
        const double elapsed_s =
            static_cast<double>(now_ns - lowcmd_last_report_ns_) / 1e9;
        std::cout
            << "[LowCmd TX] transport="
            << (lowcmd_udp_enabled_ ? "udp" : "zmq")
            << " rate="
            << (elapsed_s > 0.0 ? interval_attempts / elapsed_s : 0.0)
            << "Hz success=" << interval_successes
            << " fail=" << interval_failures
            << " max_gap=" << lowcmd_interval_max_ns_ / 1e6 << "ms"
            << " max_lock=" << lowcmd_lock_wait_max_ns_ / 1e6 << "ms"
            << " max_work=" << lowcmd_send_work_max_ns_ / 1e6 << "ms"
            << " stream=" << lowcmd_stream_id_
            << " seq=" << lowcmd_sequence_
            << std::endl;

        lowcmd_reported_attempts_ = lowcmd_attempts_;
        lowcmd_reported_successes_ = lowcmd_send_successes_;
        lowcmd_reported_failures_ = lowcmd_send_failures_;
        lowcmd_interval_max_ns_ = 0;
        lowcmd_lock_wait_max_ns_ = 0;
        lowcmd_send_work_max_ns_ = 0;
        lowcmd_last_report_ns_ = now_ns;
    }

    static int64_t SteadyNowNs() {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(
                   std::chrono::steady_clock::now().time_since_epoch())
            .count();
    }

    std::string robot_ip_;
    int state_port_;
    int cmd_port_;

    std::unique_ptr<zmq::context_t> context_;
    std::unique_ptr<zmq::socket_t> state_socket_;
    std::unique_ptr<zmq::socket_t> cmd_socket_;
    // ZMQ sockets are NOT thread-safe. PublishLowCmd (500Hz command_writer) and
    // PublishDex3Cmd / PublishBraincoCmd (50Hz hand control) all share cmd_socket_.
    // Concurrent send() corrupts libzmq's zmq::msg_t internal state → "Assertion
    // failed: check() src/msg.cpp:414". Serialize all three with this mutex.
    std::mutex cmd_socket_mutex_;

    std::thread receiver_thread_;
    std::thread udp_receiver_thread_;
    std::atomic<bool> running_;
    std::atomic<bool> state_received_;
    std::atomic<bool> commands_enabled_;
    std::atomic<uint64_t> state_count_{0};
    std::atomic<uint64_t> cmd_count_{0};
    const uint64_t lowcmd_stream_id_;
    uint64_t lowcmd_sequence_{0};
    uint64_t lowcmd_attempts_{0};
    uint64_t lowcmd_send_successes_{0};
    uint64_t lowcmd_send_failures_{0};
    uint64_t lowcmd_consecutive_failures_{0};
    uint64_t lowcmd_reported_attempts_{0};
    uint64_t lowcmd_reported_successes_{0};
    uint64_t lowcmd_reported_failures_{0};
    uint64_t last_lowcmd_attempt_ns_{0};
    uint64_t lowcmd_last_report_ns_{0};
    uint64_t lowcmd_interval_max_ns_{0};
    uint64_t lowcmd_lock_wait_max_ns_{0};
    uint64_t lowcmd_send_work_max_ns_{0};
    std::atomic<uint64_t> lowcmd_udp_send_failures_{0};
    std::atomic<uint64_t> lowcmd_zmq_fallback_failures_{0};
    std::atomic<uint64_t> udp_state_count_{0};
    std::atomic<int64_t> last_lowstate_rx_ns_{0};
    std::atomic<int64_t> last_brainco_left_rx_ns_{0};
    std::atomic<int64_t> last_brainco_right_rx_ns_{0};
    std::atomic<uint64_t> brainco_decode_failures_{0};
    std::atomic<uint64_t> last_verbose_state_count_{0};

    // Primary real-robot LowCmd UDP transmission.  State and hand traffic
    // remain on their existing channels.
    int lowcmd_udp_socket_;
    int lowcmd_udp_port_;
    bool lowcmd_udp_enabled_;
    bool lowcmd_zmq_fallback_;
    struct sockaddr_in lowcmd_udp_target_ {};

    // UDP state reception
    int udp_socket_;
    int udp_port_;
    bool use_udp_;
    LowState_ udp_low_state_;
    IMUState_ udp_sec_imu_;
    std::mutex udp_state_mutex_;

    // Current state buffers
    LowState_ current_low_state_;
    IMUState_ current_imu_;

    mutable std::mutex brainco_state_mutex_;
    brainco_hand_state::JointState brainco_left_state_;
    brainco_hand_state::JointState brainco_right_state_;

    std::mutex callback_mutex_;
    std::function<void(const void*)> low_state_callback_;
    std::function<void(const void*)> imu_callback_;
};
