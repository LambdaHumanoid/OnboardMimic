#include <gtest/gtest.h>

#include <cstring>
#include <mutex>
#include <optional>
#include <string>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include "utils.hpp"
#include "zmq_robot_channel.hpp"

namespace {

std::optional<LowCmdTransportMetadata> TryReceiveMetadata(
    zmq::socket_t& subscriber) {
  zmq::message_t topic;
  zmq::message_t payload;
  zmq::message_t metadata;
  if (!subscriber.recv(topic, zmq::recv_flags::none).has_value()) {
    return std::nullopt;
  }
  EXPECT_TRUE(topic.more());
  EXPECT_TRUE(subscriber.recv(payload, zmq::recv_flags::none).has_value());
  EXPECT_TRUE(payload.more());
  EXPECT_TRUE(subscriber.recv(metadata, zmq::recv_flags::none).has_value());
  EXPECT_FALSE(metadata.more());
  EXPECT_EQ(std::string(
                static_cast<const char*>(topic.data()), topic.size()),
            "rt/lowcmd");
  EXPECT_EQ(metadata.size(), sizeof(LowCmdTransportMetadata));

  LowCmdTransportMetadata decoded;
  if (metadata.size() == sizeof(decoded)) {
    std::memcpy(&decoded, metadata.data(), sizeof(decoded));
  }
  return std::make_optional(decoded);
}

}  // namespace

TEST(LowCmdTransport, PublishesBackwardCompatibleMetadataTrailer) {
  zmq::context_t context(1);
  zmq::socket_t subscriber(context, zmq::socket_type::sub);
  subscriber.set(zmq::sockopt::subscribe, "rt/lowcmd");
  subscriber.set(zmq::sockopt::rcvtimeo, 200);
  subscriber.bind("tcp://127.0.0.1:*");
  const std::string endpoint =
      subscriber.get(zmq::sockopt::last_endpoint);
  const auto separator = endpoint.rfind(':');
  ASSERT_NE(separator, std::string::npos);
  const int command_port = std::stoi(endpoint.substr(separator + 1));

  ZmqRobotChannel channel;
  ASSERT_TRUE(channel.Init(
      "127.0.0.1",
      command_port + 1,
      command_port,
      false));

  LowCmd_ command;
  command.mode_pr() = 0;
  command.mode_machine() = 5;
  command.motor_cmd().at(0).mode() = 1;
  command.motor_cmd().at(0).q() = 0.25f;
  command.motor_cmd().at(0).kp() = 100.0f;

  std::optional<LowCmdTransportMetadata> first;
  for (int attempt = 0; attempt < 20 && !first.has_value(); ++attempt) {
    ASSERT_TRUE(channel.PublishLowCmd(command));
    first = TryReceiveMetadata(subscriber);
  }
  ASSERT_TRUE(first.has_value());
  EXPECT_EQ(std::string(first->magic, first->magic + 4), "LCM1");
  EXPECT_EQ(first->version, 1);
  EXPECT_NE(first->stream_id, 0u);
  EXPECT_GE(first->sequence, 1u);
  EXPECT_NE(first->send_steady_ns, 0u);

  ASSERT_TRUE(channel.PublishLowCmd(command));
  const auto second = TryReceiveMetadata(subscriber);
  ASSERT_TRUE(second.has_value());
  EXPECT_EQ(second->stream_id, first->stream_id);
  EXPECT_EQ(second->sequence, first->sequence + 1);
  EXPECT_GE(second->send_steady_ns, first->send_steady_ns);
}

TEST(LowCmdTransport, PublishesOneCompactUdpDatagram) {
  const int receiver = socket(AF_INET, SOCK_DGRAM, 0);
  ASSERT_GE(receiver, 0);
  struct sockaddr_in address {};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  address.sin_port = 0;
  ASSERT_EQ(
      bind(
          receiver,
          reinterpret_cast<struct sockaddr*>(&address),
          sizeof(address)),
      0);
  socklen_t address_size = sizeof(address);
  ASSERT_EQ(
      getsockname(
          receiver,
          reinterpret_cast<struct sockaddr*>(&address),
          &address_size),
      0);
  const int udp_port = ntohs(address.sin_port);
  struct timeval timeout {};
  timeout.tv_sec = 1;
  ASSERT_EQ(
      setsockopt(
          receiver,
          SOL_SOCKET,
          SO_RCVTIMEO,
          &timeout,
          sizeof(timeout)),
      0);

  zmq::context_t context(1);
  zmq::socket_t zmq_subscriber(context, zmq::socket_type::sub);
  zmq_subscriber.set(zmq::sockopt::subscribe, "rt/lowcmd");
  zmq_subscriber.set(zmq::sockopt::rcvtimeo, 200);
  zmq_subscriber.bind(
      "tcp://127.0.0.1:" + std::to_string(udp_port));

  ZmqRobotChannel channel;
  ASSERT_TRUE(channel.Init(
      "127.0.0.1",
      udp_port + 1,
      udp_port,
      false,
      UDP_STATE_PORT,
      udp_port));

  LowCmd_ command;
  command.mode_pr() = 3;
  command.mode_machine() = 7;
  command.motor_cmd().at(0).mode() = 1;
  command.motor_cmd().at(0).q() = 0.25f;
  command.motor_cmd().at(0).dq() = -0.5f;
  command.motor_cmd().at(0).tau() = 1.25f;
  command.motor_cmd().at(0).kp() = 100.0f;
  command.motor_cmd().at(0).kd() = 5.0f;
  command.motor_cmd().at(0).reserve() = 99;
  command.reserve().at(2) = 1234;
  command.crc() = Crc32Core(
      reinterpret_cast<uint32_t*>(&command),
      (sizeof(command) >> 2) - 1);
  // Cross-language fixture: unitree_sdk2py CRC().Crc() over the same fields
  // on the robot must produce this exact value.
  EXPECT_EQ(command.crc(), 0xa88acd98u);

  ASSERT_TRUE(channel.PublishLowCmd(command));
  LowCmdUdpPacket packet;
  const ssize_t received =
      recv(receiver, &packet, sizeof(packet), 0);
  ASSERT_EQ(received, static_cast<ssize_t>(sizeof(packet)));
  EXPECT_EQ(std::string(packet.header.magic, packet.header.magic + 4), "LCU1");
  EXPECT_EQ(packet.header.version, LOWCMD_UDP_VERSION);
  EXPECT_EQ(packet.header.motor_count, LOWCMD_UDP_MOTOR_COUNT);
  EXPECT_EQ(packet.header.mode_pr, 3);
  EXPECT_EQ(packet.header.mode_machine, 7);
  EXPECT_NE(packet.header.stream_id, 0u);
  EXPECT_EQ(packet.header.sequence, 1u);
  EXPECT_NE(packet.header.send_steady_ns, 0u);
  EXPECT_EQ(packet.header.reserve[2], 1234u);
  EXPECT_EQ(packet.header.crc, 0xa88acd98u);
  EXPECT_EQ(packet.motor_cmd[0].mode, 1);
  EXPECT_FLOAT_EQ(packet.motor_cmd[0].q, 0.25f);
  EXPECT_FLOAT_EQ(packet.motor_cmd[0].dq, -0.5f);
  EXPECT_FLOAT_EQ(packet.motor_cmd[0].tau, 1.25f);
  EXPECT_FLOAT_EQ(packet.motor_cmd[0].kp, 100.0f);
  EXPECT_FLOAT_EQ(packet.motor_cmd[0].kd, 5.0f);
  EXPECT_EQ(packet.motor_cmd[0].reserve, 99u);
  EXPECT_LT(sizeof(packet), 1500u);

  // A UDP-capable deployment must not duplicate every 500 Hz LowCmd over
  // ZMQ by default.  That compatibility path is explicit opt-in only.
  zmq::message_t unexpected_zmq_topic;
  EXPECT_FALSE(
      zmq_subscriber
          .recv(unexpected_zmq_topic, zmq::recv_flags::none)
          .has_value());

  close(receiver);
}
