#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

#include "utils.hpp"
#include "zmq_robot_channel.hpp"

namespace {

struct Options {
  std::string host = "127.0.0.1";
  int state_port = 15560;
  int port = 15561;
  int udp_port = 0;
  double duration_s = 60.0;
  double rate_hz = 500.0;
  bool require_state = false;
  double max_state_age_ms = 250.0;
};

Options ParseOptions(int argc, char** argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string argument = argv[i];
    auto next = [&]() -> std::string {
      if (++i >= argc) {
        throw std::invalid_argument("missing value after " + argument);
      }
      return argv[i];
    };
    if (argument == "--host") {
      options.host = next();
    } else if (argument == "--state-port") {
      options.state_port = std::stoi(next());
    } else if (argument == "--port") {
      options.port = std::stoi(next());
    } else if (argument == "--udp-port") {
      options.udp_port = std::stoi(next());
    } else if (argument == "--duration-s") {
      options.duration_s = std::stod(next());
    } else if (argument == "--rate-hz") {
      options.rate_hz = std::stod(next());
    } else if (argument == "--require-state") {
      options.require_state = true;
    } else if (argument == "--max-state-age-ms") {
      options.max_state_age_ms = std::stod(next());
    } else if (argument == "--help" || argument == "-h") {
      std::cout
          << "Usage: lowcmd_transport_probe [--host IP] "
          << "[--state-port 15560] [--port 15561] "
          << "[--udp-port 0|15563] [--duration-s 60] [--rate-hz 500]\n"
          << "       [--require-state] [--max-state-age-ms 250]\n"
          << "Diagnostic only: all 35 motor modes are disabled. Port 5561 is "
          << "unconditionally rejected, as is production UDP port 5563.\n";
      std::exit(0);
    } else {
      throw std::invalid_argument("unknown argument: " + argument);
    }
  }
  if (options.port == 5561 || options.port == 5563) {
    throw std::invalid_argument(
        "refusing production motor-command ports 5561/5563");
  }
  if (
      options.state_port < 1 || options.state_port >= 65535
      || options.port < 1 || options.port >= 65535) {
    throw std::invalid_argument(
        "state-port and port must be within [1, 65534]");
  }
  if (
      options.udp_port < 0
      || options.udp_port >= 65535
      || options.udp_port == 5561
      || options.udp_port == 5563) {
    throw std::invalid_argument(
        "udp-port must be 0 or a non-production port within [1, 65534]");
  }
  if (
      options.duration_s <= 0.0 || options.rate_hz <= 0.0
      || options.max_state_age_ms <= 0.0) {
    throw std::invalid_argument(
        "duration, rate, and max-state-age-ms must be positive");
  }
  return options;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Options options = ParseOptions(argc, argv);
    std::cout
        << "[LowCmd Probe] diagnostic-only disabled-motor stream -> tcp://"
        << options.host << ":" << options.port
        << (options.udp_port > 0
                ? " + udp://" + options.host + ":"
                    + std::to_string(options.udp_port)
                : "")
        << " rate=" << options.rate_hz
        << "Hz duration=" << options.duration_s << "s"
        << " state=tcp://" << options.host << ":" << options.state_port
        << (options.require_state ? " (required)" : " (optional)")
        << std::endl;

    ZmqRobotChannel channel;
    if (!channel.Init(
            options.host,
            options.state_port,
            options.port,
            false,
            UDP_STATE_PORT,
            options.udp_port)) {
      std::cerr << "[LowCmd Probe] failed to initialize ZMQ channel"
                << std::endl;
      return 2;
    }

    LowCmd_ command;
    command.mode_pr() = 0;
    command.mode_machine() = 0;
    for (auto& motor : command.motor_cmd()) {
      motor.mode() = 0;
      motor.q() = 0.0f;
      motor.dq() = 0.0f;
      motor.tau() = 0.0f;
      motor.kp() = 0.0f;
      motor.kd() = 0.0f;
      motor.reserve() = 0;
    }
    command.crc() = Crc32Core(
        reinterpret_cast<uint32_t*>(&command),
        (sizeof(command) >> 2) - 1);

    using Clock = std::chrono::steady_clock;
    const auto period = std::chrono::duration<double>(1.0 / options.rate_hz);
    const auto started = Clock::now();
    auto next = started;
    uint64_t attempts = 0;
    uint64_t successes = 0;
    bool observed_state = false;
    double max_state_age_ms = 0.0;
    while (
        std::chrono::duration<double>(Clock::now() - started).count()
        < options.duration_s) {
      ++attempts;
      if (channel.PublishLowCmd(command)) {
        ++successes;
      }
      const double state_age_ms = channel.GetLastLowStateAgeMs();
      if (state_age_ms >= 0.0) {
        observed_state = true;
        max_state_age_ms = std::max(max_state_age_ms, state_age_ms);
      }
      next += std::chrono::duration_cast<Clock::duration>(period);
      std::this_thread::sleep_until(next);
    }

    const double final_state_age_ms = channel.GetLastLowStateAgeMs();
    const uint64_t state_count = channel.GetStateCount();
    const bool state_ok =
        observed_state
        && final_state_age_ms >= 0.0
        && final_state_age_ms <= options.max_state_age_ms
        && max_state_age_ms <= options.max_state_age_ms;
    std::cout
        << "[LowCmd Probe] complete attempts=" << attempts
        << " successes=" << successes
        << " failures=" << (attempts - successes)
        << " state_count=" << state_count
        << " final_state_age_ms=" << final_state_age_ms
        << " max_state_age_ms=" << max_state_age_ms
        << " state_ok=" << (state_ok ? "yes" : "no") << std::endl;
    if (successes != attempts) {
      return 3;
    }
    if (options.require_state && !state_ok) {
      return 4;
    }
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "[LowCmd Probe] ERROR: " << error.what() << std::endl;
    return 1;
  }
}
