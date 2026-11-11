#!/usr/bin/env python3
"""ZMQ-ROS2 Bridge for Unitree G1 + BrainCo hand.

Runs on the robot. Subscribes ROS2 topics (lowstate / dex3 / brainco motor_status),
forwards them to host via ZMQ; receives ZMQ commands from host and republishes them
as ROS2 topics (lowcmd / dex3 cmd / brainco joint_commands).

Wire ZMQ topic names are unchanged from the previous unitree_sdk2py-based bridge,
so host-side clients (zmq_channel.py, SONIC C++) require no change. New brainco
topics are added under `rt/brainco/...`.

Architecture:
    Robot                                       Host
    ┌─────────────────────────┐                 ┌─────────────────────────┐
    │  ROS2 (CycloneDDS)      │                 │  Control Loop           │
    │  ↕ rclpy node           │                 │  (zmq_channel etc)      │
    │  THIS SCRIPT            │  ←── ZMQ ──→    │                         │
    │  - PUB :5560 state      │                 │                         │
    │  - SUB :5561 cmd        │                 │                         │
    │  - UDP :5562 state      │                 │                         │
    │  - UDP :5563 LowCmd     │                 │                         │
    └─────────────────────────┘                 └─────────────────────────┘

Required environment on the robot:
    source /opt/ros/foxy/setup.bash
    source ~/cyclonedds_ws/install/setup.bash
    source ~/unitree-g1-brainco-hand/ros2_stark_ws/install/setup.bash
    export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
    export CYCLONEDDS_URI=file://$HOME/cyclonedds_ws/cyclonedds.xml

`robot_services.sh` prepares this environment. A conda environment is optional
and is used only when CONDA_ENV is explicitly configured there.
"""

import argparse
import math
import os
import signal
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import zmq

from lowcmd_transport import (
    CommandDisposition,
    LowCmdTransportMetadata,
    LowCmdTransportMonitor,
    LowCmdUdpPacket,
    LowCmdWatchdog,
    WatchdogDecision,
    WatchdogState,
)

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

from unitree_hg.msg import HandCmd, HandState, IMUState, LowCmd, LowState
from sensor_msgs.msg import JointState

try:
    from ros2_stark_interfaces.msg import MotorStatus as BraincoMotorStatus
    HAS_BRAINCO = True
except ImportError:
    BraincoMotorStatus = None
    HAS_BRAINCO = False

# Keep unitree_sdk2py imports only for: (1) CRC over LowCmd, (2) one-shot MotionSwitcher
from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
from unitree_sdk2py.utils.crc import CRC

from .zmq_serializer import deserialize_message, serialize_message


# UDP State Protocol - fixed binary format for low-latency transmission
UDP_STATE_MAGIC = b"STAT"
UDP_STATE_FORMAT = "<4sI35f35f4f3f3f4f3f3f"
UDP_STATE_SIZE = struct.calcsize(UDP_STATE_FORMAT)  # 368 bytes


@dataclass
class BridgeConfig:
    state_port: int = 5560
    cmd_port: int = 5561
    udp_state_port: int = 5562
    lowcmd_udp_port: int = 0
    loop_rate: float = 500.0
    enable_dex3: bool = True
    enable_brainco: bool = True
    verbose: bool = False
    enable_udp_state: bool = True
    udp_target_ip: str = ""
    publish_lowcmd: bool = True
    lowcmd_topic: str = "/lowcmd"
    lowcmd_timeout_s: float = 0.1
    lowcmd_failsafe_ramp_s: float = 0.15
    lowcmd_recovery_packets: int = 25
    lowcmd_recovery_ramp_s: float = 0.5


class Ros2Bridge(Node):
    """rclpy node holding all subscribers and publishers used by the bridge."""

    def __init__(self, config: BridgeConfig):
        super().__init__("zmq_ros2_bridge")
        self.config = config
        # Back-ref to bridge so callbacks can publish straight to ZMQ. Set by
        # ZmqRos2Bridge.init_ros2() right after instantiation.
        self.bridge = None

        # Caches (populated by subscription callbacks; consumed by main thread)
        self.cached_lowstate: Optional[LowState] = None
        self.cached_secondary_imu: Optional[IMUState] = None
        self.cached_left_hand: Optional[HandState] = None
        self.cached_right_hand: Optional[HandState] = None
        self.cached_brainco_left = None
        self.cached_brainco_right = None
        self.cached_lowstate_changed = False  # Whether main loop should send via UDP

        # Use BEST_EFFORT for high-rate state to match unitree firmware's QoS
        state_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        cmd_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        # Subscriptions: state in
        self.create_subscription(LowState, "/lowstate", self._lowstate_cb, state_qos)
        self.create_subscription(
            IMUState, "/secondary_imu", self._secondary_imu_cb, state_qos
        )
        if config.enable_dex3:
            self.create_subscription(
                HandState, "/dex3/left/state", self._left_hand_cb, state_qos
            )
            self.create_subscription(
                HandState, "/dex3/right/state", self._right_hand_cb, state_qos
            )
        if config.enable_brainco and HAS_BRAINCO:
            self.create_subscription(
                BraincoMotorStatus, "/motor_status", self._brainco_left_cb, state_qos
            )
            self.create_subscription(
                BraincoMotorStatus, "/motor_status_r", self._brainco_right_cb, state_qos
            )

        # Publishers: command out
        self.lowcmd_pub = self.create_publisher(LowCmd, config.lowcmd_topic, cmd_qos)
        if config.enable_dex3:
            self.left_hand_cmd_pub = self.create_publisher(
                HandCmd, "/dex3/left/cmd", cmd_qos
            )
            self.right_hand_cmd_pub = self.create_publisher(
                HandCmd, "/dex3/right/cmd", cmd_qos
            )
        else:
            self.left_hand_cmd_pub = None
            self.right_hand_cmd_pub = None
        if config.enable_brainco:
            self.brainco_left_cmd_pub = self.create_publisher(
                JointState, "/joint_commands_left", cmd_qos
            )
            self.brainco_right_cmd_pub = self.create_publisher(
                JointState, "/joint_commands_right", cmd_qos
            )
        else:
            self.brainco_left_cmd_pub = None
            self.brainco_right_cmd_pub = None

    # --- Subscription callbacks ---
    # PERF: publish state to ZMQ directly from the rclpy spin thread instead of
    # routing through the bridge main loop. rclpy.spin() is single-threaded so
    # all callbacks (and thus state_pub access) stay on one thread → safe to
    # share state_pub here without a mutex. Removes ~36% of main-loop cycle
    # (lifts ros2_writes from ~200Hz toward 500Hz target).
    # cached_* fields kept for UDP path which still runs in main loop.
    def _publish_state(self, topic, msg):
        if self.bridge is not None and self.bridge.state_pub is not None:
            self.bridge.publish_state_zmq(topic, msg)

    def _lowstate_cb(self, msg: LowState):
        self.cached_lowstate = msg
        self.cached_lowstate_changed = True
        # Motor command continuity has priority over host telemetry.  Drive the
        # ROS2 LowCmd publisher from the robot's own LowState cadence rather
        # than the Python command-receive loop; a UDP/ZMQ arrival burst can
        # therefore never starve DDS command refresh.
        if self.bridge is not None:
            self.bridge.publish_lowcmd_on_state_tick()
        self._publish_state("rt/lowstate", msg)

    def _secondary_imu_cb(self, msg: IMUState):
        self.cached_secondary_imu = msg
        self._publish_state("rt/secondary_imu", msg)

    def _left_hand_cb(self, msg: HandState):
        self.cached_left_hand = msg
        self._publish_state("rt/dex3/left/state", msg)

    def _right_hand_cb(self, msg: HandState):
        self.cached_right_hand = msg
        self._publish_state("rt/dex3/right/state", msg)

    def _brainco_left_cb(self, msg):
        self.cached_brainco_left = msg
        self._publish_state("rt/brainco/left/state", msg)

    def _brainco_right_cb(self, msg):
        self.cached_brainco_right = msg
        self._publish_state("rt/brainco/right/state", msg)


class ZmqRos2Bridge:
    """Bridge between local ROS2 topics and remote ZMQ communication."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.running = False

        self.context: Optional[zmq.Context] = None
        self.state_pub: Optional[zmq.Socket] = None
        self.cmd_sub: Optional[zmq.Socket] = None

        self.udp_socket: Optional[socket.socket] = None
        self.udp_target_addr: Optional[tuple] = None
        self.lowcmd_udp_socket: Optional[socket.socket] = None
        self.lowcmd_udp_peer_ip: Optional[str] = None

        self.node: Optional[Ros2Bridge] = None
        self.spin_thread: Optional[threading.Thread] = None

        self.crc = CRC()

        # Cache last-received DDS-class LowCmd for high-rate publish (with CRC)
        self.last_lowcmd: Optional[LowCmd] = None
        self._latest_live_lowcmd: Optional[LowCmd] = None
        self._failsafe_source_lowcmd: Optional[LowCmd] = None
        self._failsafe_target_lowcmd: Optional[LowCmd] = None
        self._recovery_source_lowcmd: Optional[LowCmd] = None
        self._watchdog_state_seen = WatchdogState.IDLE
        self.lowcmd_transport = LowCmdTransportMonitor()
        self.lowcmd_watchdog = LowCmdWatchdog(
            timeout_s=config.lowcmd_timeout_s,
            failsafe_ramp_s=config.lowcmd_failsafe_ramp_s,
            recovery_packets=config.lowcmd_recovery_packets,
            recovery_ramp_s=config.lowcmd_recovery_ramp_s,
        )
        self._crc_dds = None  # lazy-init DDS LowCmd_ reused by _publish_lowcmd_with_crc
        self._last_lowcmd_publish_ns: Optional[int] = None
        self._max_lowcmd_publish_gap_ms = 0.0
        self._lowcmd_publish_gap_counts = {
            threshold: 0 for threshold in (20.0, 50.0, 100.0)
        }
        # Validate host CRCs before taking the fast path.  Recomputing the same
        # cached command on every ROS2 publish held the bridge near 295 Hz.
        self._force_trust_host_crc = os.environ.get(
            "BRIDGE_TRUST_HOST_CRC", "0"
        ).lower() in ("1", "true", "yes")
        self._host_crc_stream_id: Optional[int] = None
        self._host_crc_verified = self._force_trust_host_crc
        self._host_crc_match_count = 0
        self._host_crc_check_count = 0
        if self._force_trust_host_crc:
            print("[Bridge] BRIDGE_TRUST_HOST_CRC=1 → skipping bridge-side CRC recompute")

        self.stats = {
            "state_tx": 0,
            "cmd_rx": 0,
            "ros2_writes": 0,
            "lowcmd_ros2_writes": 0,
            "errors": 0,
            "last_state_time": 0.0,
            "last_cmd_time": 0.0,
            "lowcmd_invalid_metadata": 0,
            "lowcmd_udp_datagrams": 0,
            "lowcmd_udp_rx": 0,
            "lowcmd_udp_coalesced": 0,
            "lowcmd_udp_invalid": 0,
            "lowcmd_udp_foreign_peer": 0,
            "lowcmd_zmq_fallback_ignored": 0,
            "lowcmd_crc_rejected": 0,
        }

    def init_zmq(self) -> bool:
        try:
            self.context = zmq.Context()

            self.state_pub = self.context.socket(zmq.PUB)
            # State is snapshot data: cap both ends of the ZMQ path so a burst
            # cannot build a long queue of stale LowState/IMU messages.  The host
            # receiver uses the same HWM and publishes callbacks in bounded batches.
            self.state_pub.setsockopt(zmq.SNDHWM, 128)
            self.state_pub.bind(f"tcp://127.0.0.1:{self.config.state_port}")
            print(f"[Bridge] State PUB bound to tcp://127.0.0.1:{self.config.state_port}")

            self.cmd_sub = self.context.socket(zmq.SUB)
            self.cmd_sub.setsockopt(zmq.RCVTIMEO, 1)
            # Commands are multipart messages: [topic, payload]. ZMQ_CONFLATE
            # does not support multipart sockets and can retain only one frame,
            # separating a topic from its payload or starving hand topics behind
            # the 500 Hz lowcmd stream. Keep a bounded queue and coalesce per
            # topic in run() instead.
            self.cmd_sub.setsockopt(zmq.RCVHWM, 256)
            self.cmd_sub.setsockopt_string(zmq.SUBSCRIBE, "rt/lowcmd")
            self.cmd_sub.setsockopt_string(zmq.SUBSCRIBE, "rt/dex3/left/cmd")
            self.cmd_sub.setsockopt_string(zmq.SUBSCRIBE, "rt/dex3/right/cmd")
            self.cmd_sub.setsockopt_string(zmq.SUBSCRIBE, "rt/brainco/left/cmd")
            self.cmd_sub.setsockopt_string(zmq.SUBSCRIBE, "rt/brainco/right/cmd")
            self.cmd_sub.bind(f"tcp://127.0.0.1:{self.config.cmd_port}")
            print(f"[Bridge] Command SUB bound to tcp://127.0.0.1:{self.config.cmd_port}")
            return True
        except zmq.ZMQError as e:
            print(f"[Bridge] ZMQ init error: {e}")
            return False

    def init_lowcmd_udp(self) -> bool:
        """Bind the latest-only compact LowCmd datagram receiver."""
        if self.config.lowcmd_udp_port <= 0:
            return True
        try:
            self.lowcmd_udp_socket = socket.socket(
                socket.AF_INET, socket.SOCK_DGRAM
            )
            self.lowcmd_udp_socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
            )
            # Keep the queue bounded.  The main loop drains it completely and
            # applies only the newest admissible command.
            self.lowcmd_udp_socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024
            )
            self.lowcmd_udp_socket.bind(
                ("127.0.0.1", self.config.lowcmd_udp_port)
            )
            self.lowcmd_udp_socket.setblocking(False)
            print(
                "[Bridge] LowCmd UDP receiver bound to "
                f"udp://127.0.0.1:{self.config.lowcmd_udp_port} "
                "(single-datagram latest-only mode)"
            )
            return True
        except OSError as error:
            print(f"[Bridge] LowCmd UDP init error: {error}")
            if self.lowcmd_udp_socket is not None:
                self.lowcmd_udp_socket.close()
                self.lowcmd_udp_socket = None
            return False

    def init_udp(self, target_ip: str) -> bool:
        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
            self.udp_target_addr = (target_ip, self.config.udp_state_port)
            print(
                f"[Bridge] UDP state sender initialized, target: "
                f"{target_ip}:{self.config.udp_state_port}"
            )
            return True
        except Exception as e:
            print(f"[Bridge] UDP init error: {e}")
            return False

    def init_ros2(self) -> bool:
        try:
            if not rclpy.ok():
                rclpy.init()
            self.node = Ros2Bridge(self.config)
            self.node.bridge = self  # let callbacks publish state directly to ZMQ
            self.spin_thread = threading.Thread(
                target=rclpy.spin, args=(self.node,), daemon=True
            )
            self.spin_thread.start()
            sub_summary = "/lowstate /secondary_imu"
            if self.config.enable_dex3:
                sub_summary += " /dex3/left/state /dex3/right/state"
            if self.config.enable_brainco:
                if HAS_BRAINCO:
                    sub_summary += " /motor_status /motor_status_r"
                else:
                    print(
                        "[Bridge] Warning: --enable-brainco set but ros2_stark_interfaces not found, "
                        "skipping brainco state subscriptions"
                    )
            print(f"[Bridge] ROS2 node initialized; subscribed: {sub_summary}")
            return True
        except Exception as e:
            print(f"[Bridge] ROS2 init error: {e}")
            return False

    @staticmethod
    def pack_state_udp(lowstate: LowState, secondary_imu: Optional[IMUState]) -> bytes:
        """Pack state into fixed binary format for low-latency UDP."""
        q = [lowstate.motor_state[i].q for i in range(35)]
        dq = [lowstate.motor_state[i].dq for i in range(35)]
        imu_quat = [lowstate.imu_state.quaternion[i] for i in range(4)]
        imu_gyro = [lowstate.imu_state.gyroscope[i] for i in range(3)]
        imu_accel = [lowstate.imu_state.accelerometer[i] for i in range(3)]
        if secondary_imu is not None:
            sec_quat = [secondary_imu.quaternion[i] for i in range(4)]
            sec_gyro = [secondary_imu.gyroscope[i] for i in range(3)]
            sec_accel = [secondary_imu.accelerometer[i] for i in range(3)]
        else:
            sec_quat = [1.0, 0.0, 0.0, 0.0]
            sec_gyro = [0.0, 0.0, 0.0]
            sec_accel = [0.0, 0.0, 0.0]
        return struct.pack(
            UDP_STATE_FORMAT,
            UDP_STATE_MAGIC,
            int(lowstate.tick),
            *q,
            *dq,
            *imu_quat,
            *imu_gyro,
            *imu_accel,
            *sec_quat,
            *sec_gyro,
            *sec_accel,
        )

    def send_state_udp(self, lowstate: LowState, secondary_imu: Optional[IMUState]):
        if self.udp_socket and self.udp_target_addr:
            try:
                data = self.pack_state_udp(lowstate, secondary_imu)
                self.udp_socket.sendto(data, self.udp_target_addr)
            except Exception as e:
                if self.config.verbose:
                    print(f"[Bridge] UDP send error: {e}")

    def publish_state_zmq(self, topic: str, msg) -> None:
        try:
            data = serialize_message(topic, msg)
            self.state_pub.send_multipart(
                [topic.encode("utf-8"), data], zmq.NOBLOCK
            )
            self.stats["state_tx"] += 1
            self.stats["last_state_time"] = time.time()
        except zmq.ZMQError as e:
            self.stats["errors"] += 1
            if self.config.verbose:
                print(f"[Bridge] State publish error: {e}")
        except Exception as e:
            self.stats["errors"] += 1
            if self.config.verbose:
                print(f"[Bridge] State serialize error ({topic}): {e}")

    def receive_command(self, blocking: bool = False) -> Optional[tuple]:
        """Read one msg. blocking=True respects RCVTIMEO (1ms) — lets the main
        loop tick be driven by incoming cmds instead of busy-spinning."""
        try:
            flags = 0 if blocking else zmq.NOBLOCK
            parts = self.cmd_sub.recv_multipart(flags)
            if len(parts) >= 2:
                topic = parts[0].decode("utf-8")
                data = parts[1]
                self.stats["cmd_rx"] += 1
                self.stats["last_cmd_time"] = time.time()
                if topic == "rt/lowcmd":
                    metadata = None
                    if len(parts) >= 3:
                        metadata = LowCmdTransportMetadata.decode(parts[2])
                        if metadata is None:
                            self.stats["lowcmd_invalid_metadata"] += 1
                            return (
                                topic,
                                data,
                                WatchdogDecision(
                                    CommandDisposition.HOLD,
                                    self.lowcmd_watchdog.state,
                                    "invalid_transport_metadata",
                                ),
                            )
                        self._select_host_crc_stream(metadata.stream_id)
                        if self.config.lowcmd_udp_port > 0:
                            # A new SONIC sender emits this ZMQ copy only so an
                            # older bridge remains usable.  The compact UDP
                            # datagram is authoritative here; allowing both
                            # copies to touch the watchdog would hide UDP loss
                            # behind TCP and reintroduce the original failure.
                            self.stats["lowcmd_zmq_fallback_ignored"] += 1
                            return (
                                topic,
                                data,
                                WatchdogDecision(
                                    CommandDisposition.HOLD,
                                    self.lowcmd_watchdog.state,
                                    "udp_authoritative",
                                ),
                            )
                    receive_ns = time.monotonic_ns()
                    self.lowcmd_transport.observe(metadata, receive_ns)
                    decision = self.lowcmd_watchdog.observe(
                        metadata, receive_ns / 1e9
                    )
                    return topic, data, decision
                return topic, data, None
        except zmq.Again:
            pass
        except zmq.ZMQError as e:
            self.stats["errors"] += 1
            if self.config.verbose:
                print(f"[Bridge] Command receive error: {e}")
        return None

    def receive_lowcmd_udp(
        self,
    ) -> Optional[tuple]:
        """Drain UDP cheaply, then fully decode only the newest datagram.

        A Wi-Fi delivery burst may contain dozens of obsolete 500 Hz packets.
        Constructing 35 ROS motor objects and checking CRC for every queued
        packet created a positive feedback loop that starved DDS publication.
        Header-only timing observation remains per datagram; safety validation
        and ROS conversion happen once for the newest candidate.
        """
        if self.lowcmd_udp_socket is None:
            return None

        latest = None
        for _ in range(256):
            try:
                payload, peer = self.lowcmd_udp_socket.recvfrom(2048)
            except BlockingIOError:
                break
            except OSError as error:
                self.stats["errors"] += 1
                if self.config.verbose:
                    print(f"[Bridge] LowCmd UDP receive error: {error}")
                break

            self.stats["lowcmd_udp_datagrams"] += 1
            peer_ip = str(peer[0])
            if (
                self.lowcmd_udp_peer_ip is not None
                and peer_ip != self.lowcmd_udp_peer_ip
            ):
                self.stats["lowcmd_udp_foreign_peer"] += 1
                continue

            metadata = LowCmdUdpPacket.decode_metadata(payload)
            if metadata is None:
                self.stats["lowcmd_udp_invalid"] += 1
                continue

            receive_ns = time.monotonic_ns()
            if self.lowcmd_udp_peer_ip is not None:
                self.lowcmd_transport.observe(metadata, receive_ns)
            if latest is not None:
                self.stats["lowcmd_udp_coalesced"] += 1
            latest = (payload, peer_ip, metadata, receive_ns)

        if latest is None:
            return None

        payload, peer_ip, metadata, receive_ns = latest
        packet = LowCmdUdpPacket.decode(payload)
        if packet is None:
            self.stats["lowcmd_udp_invalid"] += 1
            return None

        self._select_host_crc_stream(packet.metadata.stream_id)

        # Do not let a merely well-shaped datagram claim the peer lock.
        # It must also reconstruct as a finite LowCmd with a valid Unitree CRC
        # before its source address can become authoritative.
        ros2_cmd = self._lowcmd_udp_to_ros(packet)
        if ros2_cmd is None:
            return None

        if self.lowcmd_udp_peer_ip is None:
            self.lowcmd_udp_peer_ip = peer_ip
            print(f"[Bridge] LowCmd UDP peer locked to {peer_ip}")
            self.lowcmd_transport.observe(metadata, receive_ns)
        elif peer_ip != self.lowcmd_udp_peer_ip:
            # The peer can only change between the drain and this validation
            # if another thread mutates it; keep this final fail-closed check.
            self.stats["lowcmd_udp_foreign_peer"] += 1
            return None

        self.stats["cmd_rx"] += 1
        self.stats["lowcmd_udp_rx"] += 1
        self.stats["last_cmd_time"] = time.time()
        decision = self.lowcmd_watchdog.observe(
            packet.metadata, receive_ns / 1e9
        )
        if decision.disposition == CommandDisposition.HOLD:
            return None
        return ros2_cmd, decision

    def _lowcmd_udp_to_ros(
        self,
        packet: LowCmdUdpPacket,
    ) -> Optional[LowCmd]:
        """Convert and CRC-check one compact packet before it gains freshness."""
        ros2_cmd = LowCmd()
        ros2_cmd.mode_pr = int(packet.mode_pr)
        ros2_cmd.mode_machine = int(packet.mode_machine)
        for index, source in enumerate(packet.motor_cmd):
            target = ros2_cmd.motor_cmd[index]
            target.mode = int(source.mode)
            target.q = float(source.q)
            target.dq = float(source.dq)
            target.tau = float(source.tau)
            target.kp = float(source.kp)
            target.kd = float(source.kd)
            target.reserve = int(source.reserve)
        for index, value in enumerate(packet.reserve):
            ros2_cmd.reserve[index] = int(value)
        if not self._prepare_lowcmd_crc(ros2_cmd, int(packet.crc)):
            return None
        return ros2_cmd

    def forward_lowcmd_udp(
        self,
        ros2_cmd: LowCmd,
        disposition: CommandDisposition,
    ) -> None:
        """Store an already validated compact LowCmd for ROS publication."""
        self._latest_live_lowcmd = ros2_cmd
        if disposition in (
            CommandDisposition.APPLY,
            CommandDisposition.APPLY_RECOVERY,
        ):
            self.last_lowcmd = ros2_cmd

    def forward_command(
        self,
        topic: str,
        data: bytes,
        lowcmd_disposition: Optional[CommandDisposition] = None,
    ):
        try:
            msg = deserialize_message(topic, data)

            if topic == "rt/lowcmd":
                ros2_cmd = LowCmd()
                ros2_cmd.mode_pr = int(msg.mode_pr)
                ros2_cmd.mode_machine = int(msg.mode_machine)
                for i in range(min(len(msg.motor_cmd), 35)):
                    ros2_cmd.motor_cmd[i].mode = int(msg.motor_cmd[i].mode)
                    ros2_cmd.motor_cmd[i].q = float(msg.motor_cmd[i].q)
                    ros2_cmd.motor_cmd[i].dq = float(msg.motor_cmd[i].dq)
                    ros2_cmd.motor_cmd[i].tau = float(msg.motor_cmd[i].tau)
                    ros2_cmd.motor_cmd[i].kp = float(msg.motor_cmd[i].kp)
                    ros2_cmd.motor_cmd[i].kd = float(msg.motor_cmd[i].kd)
                    ros2_cmd.motor_cmd[i].reserve = int(msg.motor_cmd[i].reserve)
                for i in range(min(len(msg.reserve), 4)):
                    ros2_cmd.reserve[i] = int(msg.reserve[i])
                if not self._prepare_lowcmd_crc(
                    ros2_cmd,
                    int(getattr(msg, "crc", 0)) & 0xFFFFFFFF,
                ):
                    return
                self._latest_live_lowcmd = ros2_cmd
                if lowcmd_disposition in (
                    None,
                    CommandDisposition.APPLY,
                ):
                    self.last_lowcmd = ros2_cmd

            elif (
                topic == "rt/dex3/left/cmd"
                and self.node
                and self.node.left_hand_cmd_pub
            ):
                ros2_cmd = HandCmd()
                for i in range(min(len(msg.motor_cmd), 7)):
                    ros2_cmd.motor_cmd[i].mode = int(msg.motor_cmd[i].mode)
                    ros2_cmd.motor_cmd[i].q = float(msg.motor_cmd[i].q)
                    ros2_cmd.motor_cmd[i].dq = float(msg.motor_cmd[i].dq)
                    ros2_cmd.motor_cmd[i].tau = float(msg.motor_cmd[i].tau)
                    ros2_cmd.motor_cmd[i].kp = float(msg.motor_cmd[i].kp)
                    ros2_cmd.motor_cmd[i].kd = float(msg.motor_cmd[i].kd)
                self.node.left_hand_cmd_pub.publish(ros2_cmd)
                self.stats["ros2_writes"] += 1

            elif (
                topic == "rt/dex3/right/cmd"
                and self.node
                and self.node.right_hand_cmd_pub
            ):
                ros2_cmd = HandCmd()
                for i in range(min(len(msg.motor_cmd), 7)):
                    ros2_cmd.motor_cmd[i].mode = int(msg.motor_cmd[i].mode)
                    ros2_cmd.motor_cmd[i].q = float(msg.motor_cmd[i].q)
                    ros2_cmd.motor_cmd[i].dq = float(msg.motor_cmd[i].dq)
                    ros2_cmd.motor_cmd[i].tau = float(msg.motor_cmd[i].tau)
                    ros2_cmd.motor_cmd[i].kp = float(msg.motor_cmd[i].kp)
                    ros2_cmd.motor_cmd[i].kd = float(msg.motor_cmd[i].kd)
                self.node.right_hand_cmd_pub.publish(ros2_cmd)
                self.stats["ros2_writes"] += 1

            elif topic == "rt/brainco/left/cmd" and self.node and self.node.brainco_left_cmd_pub:
                self.node.brainco_left_cmd_pub.publish(self._build_joint_state(msg.position))
                self.stats["ros2_writes"] += 1

            elif topic == "rt/brainco/right/cmd" and self.node and self.node.brainco_right_cmd_pub:
                self.node.brainco_right_cmd_pub.publish(self._build_joint_state(msg.position))
                self.stats["ros2_writes"] += 1

        except Exception as e:
            self.stats["errors"] += 1
            if self.config.verbose:
                print(f"[Bridge] Forward command error ({topic}): {e}")

    def _build_joint_state(self, position) -> JointState:
        js = JointState()
        js.header.stamp = self.node.get_clock().now().to_msg()
        js.name = ["thumb", "thumb_aux", "index", "middle", "ring", "pinky"]
        js.position = [float(v) for v in position]
        return js

    def _recompute_lowcmd_crc(self, ros: LowCmd) -> None:
        """Update ``ros.crc`` using the Unitree DDS LowCmd binary layout."""
        if self._crc_dds is None:
            self._crc_dds = unitree_hg_msg_dds__LowCmd_()
        dds = self._crc_dds
        dds.mode_pr = ros.mode_pr
        dds.mode_machine = ros.mode_machine
        for i in range(35):
            d = dds.motor_cmd[i]
            r = ros.motor_cmd[i]
            d.mode = r.mode
            d.q = r.q
            d.dq = r.dq
            d.tau = r.tau
            d.kp = r.kp
            d.kd = r.kd
            d.reserve = r.reserve
        for i in range(4):
            dds.reserve[i] = ros.reserve[i]
        ros.crc = int(self.crc.Crc(dds))

    def _prepare_lowcmd_crc(self, ros: LowCmd, host_crc: int) -> bool:
        """Validate host CRCs, then use them without recomputing every publish."""
        ros.crc = host_crc
        if self._force_trust_host_crc:
            return True

        self._host_crc_check_count += 1
        should_check = (
            not self._host_crc_verified
            or self._host_crc_check_count % 500 == 0
        )
        if not should_check:
            return True

        self._recompute_lowcmd_crc(ros)
        computed_crc = int(ros.crc) & 0xFFFFFFFF
        if computed_crc == host_crc:
            self._host_crc_match_count += 1
            if (
                not self._host_crc_verified
                and self._host_crc_match_count >= 16
            ):
                self._host_crc_verified = True
                print(
                    "[Bridge] host LowCmd CRC verified for 16 consecutive "
                    "frames; enabling fast publish path"
                )
            return True

        if self._host_crc_verified:
            print(
                "[Bridge] WARNING: host LowCmd CRC verification regressed; "
                "returning to bridge-side validation"
            )
        self._host_crc_verified = False
        self._host_crc_match_count = 0
        self.stats["lowcmd_crc_rejected"] += 1
        rejected = self.stats["lowcmd_crc_rejected"]
        if rejected <= 5 or rejected % 500 == 0:
            print(
                "[Bridge] ERROR: rejected LowCmd with invalid host CRC "
                f"(count={rejected})"
            )
        return False

    def _select_host_crc_stream(self, stream_id: int) -> None:
        """Require a fresh CRC verification run for every publisher process."""
        selected = int(stream_id)
        if self._host_crc_stream_id == selected:
            return
        previous = self._host_crc_stream_id
        self._host_crc_stream_id = selected
        self._host_crc_verified = self._force_trust_host_crc
        self._host_crc_match_count = 0
        self._host_crc_check_count = 0
        if previous is not None:
            print(
                "[Bridge] LowCmd publisher stream changed; resetting host "
                "CRC verification"
            )

    @staticmethod
    def _copy_lowcmd(source: LowCmd) -> LowCmd:
        copied = LowCmd()
        copied.mode_pr = int(source.mode_pr)
        copied.mode_machine = int(source.mode_machine)
        for i in range(35):
            src = source.motor_cmd[i]
            dst = copied.motor_cmd[i]
            dst.mode = int(src.mode)
            dst.q = float(src.q)
            dst.dq = float(src.dq)
            dst.tau = float(src.tau)
            dst.kp = float(src.kp)
            dst.kd = float(src.kd)
            dst.reserve = int(src.reserve)
        for i in range(4):
            copied.reserve[i] = int(source.reserve[i])
        copied.crc = int(source.crc)
        return copied

    def _make_damping_lowcmd(
        self,
        source: LowCmd,
        use_measured_position: bool,
    ) -> LowCmd:
        damping = LowCmd()
        damping.mode_pr = int(source.mode_pr)
        damping.mode_machine = int(source.mode_machine)
        measured = (
            self.node.cached_lowstate
            if self.node is not None
            else None
        )
        for i in range(35):
            src = source.motor_cmd[i]
            dst = damping.motor_cmd[i]
            enabled = int(src.mode) != 0
            dst.mode = 1 if enabled else 0
            measured_q = None
            if (
                use_measured_position
                and measured is not None
                and i < len(measured.motor_state)
            ):
                candidate = float(measured.motor_state[i].q)
                if math.isfinite(candidate):
                    measured_q = candidate
            dst.q = float(src.q) if measured_q is None else measured_q
            dst.dq = 0.0
            dst.tau = 0.0
            dst.kp = 0.0
            dst.kd = 8.0 if enabled else 0.0
            dst.reserve = 0
        for i in range(4):
            damping.reserve[i] = 0
        self._recompute_lowcmd_crc(damping)
        return damping

    def _blend_lowcmd(
        self,
        source: LowCmd,
        target: LowCmd,
        target_weight: float,
    ) -> LowCmd:
        weight = min(1.0, max(0.0, float(target_weight)))
        inverse = 1.0 - weight
        blended = LowCmd()
        blended.mode_pr = int(target.mode_pr)
        blended.mode_machine = int(target.mode_machine)
        for i in range(35):
            src = source.motor_cmd[i]
            dst_target = target.motor_cmd[i]
            dst = blended.motor_cmd[i]
            dst.mode = int(dst_target.mode)
            dst.q = inverse * float(src.q) + weight * float(dst_target.q)
            dst.dq = inverse * float(src.dq) + weight * float(dst_target.dq)
            dst.tau = inverse * float(src.tau) + weight * float(dst_target.tau)
            dst.kp = inverse * float(src.kp) + weight * float(dst_target.kp)
            dst.kd = inverse * float(src.kd) + weight * float(dst_target.kd)
            dst.reserve = int(dst_target.reserve)
        for i in range(4):
            blended.reserve[i] = int(target.reserve[i])
        self._recompute_lowcmd_crc(blended)
        return blended

    def _update_lowcmd_watchdog_output(self, now_s: float) -> None:
        self.lowcmd_watchdog.advance(now_s)
        state = self.lowcmd_watchdog.state

        if state != self._watchdog_state_seen:
            if state == WatchdogState.ACTIVE:
                print("[Bridge] LowCmd watchdog ACTIVE")
                self._failsafe_source_lowcmd = None
                self._failsafe_target_lowcmd = None
                self._recovery_source_lowcmd = None
            elif state == WatchdogState.FAILING:
                if self.last_lowcmd is not None:
                    self._failsafe_source_lowcmd = self._copy_lowcmd(
                        self.last_lowcmd
                    )
                    self._failsafe_target_lowcmd = self._make_damping_lowcmd(
                        self._failsafe_source_lowcmd,
                        use_measured_position=False,
                    )
                print(
                    "[Bridge] WARNING: LowCmd stream stale; ramping to "
                    "damping and latching recovery"
                )
            elif state == WatchdogState.LATCHED:
                if self._failsafe_target_lowcmd is not None:
                    self.last_lowcmd = self._copy_lowcmd(
                        self._failsafe_target_lowcmd
                    )
                print(
                    "[Bridge] ERROR: LowCmd failsafe latched; packets from "
                    "the failed stream cannot auto-rearm control"
                )
            elif state == WatchdogState.RECOVERING:
                if self._latest_live_lowcmd is not None:
                    self._recovery_source_lowcmd = self._make_damping_lowcmd(
                        self._latest_live_lowcmd,
                        use_measured_position=True,
                    )
                print(
                    "[Bridge] New LowCmd stream validated; smoothly "
                    "recovering from measured-position damping"
                )
            self._watchdog_state_seen = state

        if (
            state == WatchdogState.FAILING
            and self._failsafe_source_lowcmd is not None
            and self._failsafe_target_lowcmd is not None
        ):
            self.last_lowcmd = self._blend_lowcmd(
                self._failsafe_source_lowcmd,
                self._failsafe_target_lowcmd,
                self.lowcmd_watchdog.failsafe_alpha(now_s),
            )
        elif (
            state == WatchdogState.RECOVERING
            and self._latest_live_lowcmd is not None
        ):
            if self._recovery_source_lowcmd is None:
                self._recovery_source_lowcmd = self._make_damping_lowcmd(
                    self._latest_live_lowcmd,
                    use_measured_position=True,
                )
            self.last_lowcmd = self._blend_lowcmd(
                self._recovery_source_lowcmd,
                self._latest_live_lowcmd,
                self.lowcmd_watchdog.recovery_alpha(now_s),
            )

    def publish_lowcmd_on_state_tick(self):
        """Publish the cached command at the robot's LowState callback cadence."""
        command = self.last_lowcmd
        if command is None or self.node is None:
            return
        if not self.config.publish_lowcmd:
            return
        publish_ns = time.monotonic_ns()
        if self._last_lowcmd_publish_ns is not None:
            gap_ms = (
                publish_ns - self._last_lowcmd_publish_ns
            ) / 1e6
            if gap_ms >= 0.0:
                self._max_lowcmd_publish_gap_ms = max(
                    self._max_lowcmd_publish_gap_ms,
                    gap_ms,
                )
                for threshold in self._lowcmd_publish_gap_counts:
                    if gap_ms > threshold:
                        self._lowcmd_publish_gap_counts[threshold] += 1
        self._last_lowcmd_publish_ns = publish_ns
        self.node.lowcmd_pub.publish(command)
        self.stats["ros2_writes"] += 1
        self.stats["lowcmd_ros2_writes"] += 1

    def run(self):
        print("[Bridge] Starting main loop...")
        self.running = True
        loop_period = 1.0 / self.config.loop_rate
        last_stat_time = time.time()
        zmq_send_interval = 1.0 / 500.0
        last_zmq_send_time = 0.0

        # IDs of brainco state we last published, to avoid resending unchanged frames
        last_brainco_left_id = None
        last_brainco_right_id = None
        last_left_hand_id = None
        last_right_hand_id = None

        while self.running:
            loop_start = time.time()

            # --- State path: read caches populated by ROS2 callbacks, send via ZMQ/UDP ---
            now = time.time()

            # UDP low-latency: send on every change (callback set the flag)
            if (
                self.config.enable_udp_state
                and self.udp_socket
                and self.node is not None
                and self.node.cached_lowstate is not None
                and self.node.cached_lowstate_changed
            ):
                self.send_state_udp(
                    self.node.cached_lowstate, self.node.cached_secondary_imu
                )
                self.node.cached_lowstate_changed = False

            # NOTE: ZMQ state publish (rt/lowstate, rt/secondary_imu,
            # rt/dex3/{left,right}/state, rt/brainco/{left,right}/state) has
            # been moved INTO the rclpy subscription callbacks (see
            # Ros2Bridge._publish_state). That offloads ~36% of this loop's
            # cycle onto the spin thread and lets ros2_writes reach 500Hz.
            # Main loop now only handles UDP (optional) + cmd forwarding + lowcmd.


            # --- Command path: bounded drain, coalesced independently by topic ---
            udp_lowcmd = self.receive_lowcmd_udp()
            if udp_lowcmd is not None:
                ros2_cmd, decision = udp_lowcmd
                self.forward_lowcmd_udp(ros2_cmd, decision.disposition)

            # One blocking receive keeps the loop command-driven. Drain only the
            # messages already queued (up to a hard cap), then forward the newest
            # lowcmd and newest command for each hand. This preserves multipart
            # framing and prevents the 500 Hz lowcmd topic from starving either
            # BrainCo hand.
            latest_cmds = {}
            cmd_result = self.receive_command(blocking=True)
            if cmd_result:
                topic, data, decision = cmd_result
                if (
                    decision is None
                    or decision.disposition != CommandDisposition.HOLD
                ):
                    latest_cmds[topic] = (topic, data, decision)
                for _ in range(63):
                    cmd_result = self.receive_command(blocking=False)
                    if not cmd_result:
                        break
                    topic, data, decision = cmd_result
                    if (
                        decision is None
                        or decision.disposition != CommandDisposition.HOLD
                    ):
                        latest_cmds[topic] = (topic, data, decision)
            for topic, data, decision in latest_cmds.values():
                self.forward_command(
                    topic,
                    data,
                    None if decision is None else decision.disposition,
                )

            self._update_lowcmd_watchdog_output(time.monotonic())

            # Stats every 5s
            now = time.time()
            if now - last_stat_time > 5.0:
                dt = now - last_stat_time
                dds_age_ms = (
                    0.0
                    if self._last_lowcmd_publish_ns is None
                    else max(
                        0.0,
                        (
                            time.monotonic_ns()
                            - self._last_lowcmd_publish_ns
                        )
                        / 1e6,
                    )
                )
                self._max_lowcmd_publish_gap_ms = max(
                    self._max_lowcmd_publish_gap_ms,
                    dds_age_ms,
                )
                ros2_rate = (
                    self.stats["ros2_writes"] - getattr(self, "_last_ros2", 0)
                ) / dt
                cmd_rate = (self.stats["cmd_rx"] - getattr(self, "_last_cmd", 0)) / dt
                state_rate = (
                    self.stats["state_tx"] - getattr(self, "_last_state", 0)
                ) / dt
                self._last_ros2 = self.stats["ros2_writes"]
                self._last_cmd = self.stats["cmd_rx"]
                self._last_state = self.stats["state_tx"]
                print(
                    f"[Bridge] state_tx: {state_rate:.0f}Hz, cmd_rx: {cmd_rate:.0f}Hz, "
                    f"ros2_writes: {ros2_rate:.0f}Hz | totals: state={self.stats['state_tx']}, "
                    f"cmd={self.stats['cmd_rx']}, ros2={self.stats['ros2_writes']}, "
                    f"err={self.stats['errors']} | watchdog={self.lowcmd_watchdog.state.value} "
                    f"| udp_wire={self.stats['lowcmd_udp_datagrams']} "
                    f"udp_apply={self.stats['lowcmd_udp_rx']} "
                    f"udp_coalesce={self.stats['lowcmd_udp_coalesced']} "
                    f"udp_bad={self.stats['lowcmd_udp_invalid']} "
                    f"udp_foreign={self.stats['lowcmd_udp_foreign_peer']} "
                    f"zmq_copy_drop={self.stats['lowcmd_zmq_fallback_ignored']} "
                    f"crc_drop={self.stats['lowcmd_crc_rejected']} "
                    f"| dds_lowcmd={self.stats['lowcmd_ros2_writes']} "
                    f"age={dds_age_ms:.1f}ms "
                    f"max_gap={self._max_lowcmd_publish_gap_ms:.1f}ms "
                    f"gap>20/50/100ms="
                    f"{self._lowcmd_publish_gap_counts[20.0]}/"
                    f"{self._lowcmd_publish_gap_counts[50.0]}/"
                    f"{self._lowcmd_publish_gap_counts[100.0]} "
                    f"| {self.lowcmd_transport.summary()}"
                )
                last_stat_time = now

            elapsed = time.time() - loop_start
            if elapsed < loop_period:
                time.sleep(loop_period - elapsed)

    def stop(self):
        print("[Bridge] Stopping...")
        self.running = False

    def cleanup(self):
        if self.state_pub:
            self.state_pub.close()
        if self.cmd_sub:
            self.cmd_sub.close()
        if self.lowcmd_udp_socket:
            self.lowcmd_udp_socket.close()
            self.lowcmd_udp_socket = None
        if self.context:
            self.context.term()
        if self.node is not None:
            self.node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def _release_motion_switcher():
    """Best-effort: ask Unitree main service to release any active control mode."""
    try:
        from unitree_sdk2py.comm.motion_switcher.motion_switcher_client import (
            MotionSwitcherClient,
        )
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize

        # ChannelFactoryInitialize is a no-op if rclpy already initialized CycloneDDS;
        # we still try, to support standalone invocation.
        try:
            ChannelFactoryInitialize(0, os.environ.get("UNITREE_DDS_INTERFACE", "eth0"))
        except Exception:
            pass
        msc = MotionSwitcherClient()
        msc.SetTimeout(5.0)
        msc.Init()
        for _ in range(5):
            status, result = msc.CheckMode()
            mode_name = (result or {}).get("name", "")
            if mode_name:
                print(f"[Bridge] Found active mode: '{mode_name}', releasing...")
                msc.ReleaseMode()
                time.sleep(1)
            else:
                print("[Bridge] No active control mode")
                return
    except Exception as e:
        print(f"[Bridge] Warning: Could not check/release control mode: {e}")


def main():
    parser = argparse.ArgumentParser(description="ZMQ-ROS2 Bridge for Unitree G1 + BrainCo")
    parser.add_argument("--state-port", type=int, default=5560)
    parser.add_argument("--cmd-port", type=int, default=5561)
    parser.add_argument(
        "--lowcmd-udp-port",
        type=int,
        default=0,
        help=(
            "Compact latest-only SONIC LowCmd UDP port; 0 disables it "
            "(run.sh real SONIC uses 5563)"
        ),
    )
    parser.add_argument("--rate", type=float, default=500.0, help="Main loop rate Hz")
    parser.add_argument("--no-dex3", action="store_true", help="Disable dex3 hand bridging")
    parser.add_argument("--no-brainco", action="store_true", help="Disable brainco hand bridging")
    parser.add_argument(
        "--drop-lowcmd",
        action="store_true",
        help="Diagnostic only: receive/deserialize/CRC lowcmd but do not publish /lowcmd",
    )
    parser.add_argument(
        "--lowcmd-topic",
        default="/lowcmd",
        help="LowCmd ROS2 output topic (diagnostic override; default: /lowcmd)",
    )
    parser.add_argument(
        "--lowcmd-timeout-ms",
        type=float,
        default=100.0,
        help=(
            "Start the latched LowCmd failsafe after this timeout "
            "(default: 100 ms; <=0 disables timeout transitions)"
        ),
    )
    parser.add_argument(
        "--lowcmd-failsafe-ramp-ms",
        type=float,
        default=150.0,
        help=(
            "Ramp position gains to damping after a stale stream "
            "(default: 150 ms)"
        ),
    )
    parser.add_argument(
        "--lowcmd-recovery-packets",
        type=int,
        default=25,
        help=(
            "Consecutive packets required from a new publisher stream before "
            "recovery (default: 25)"
        ),
    )
    parser.add_argument(
        "--lowcmd-recovery-ramp-ms",
        type=float,
        default=500.0,
        help=(
            "Blend measured-position damping into a validated new stream "
            "(default: 500 ms)"
        ),
    )
    parser.add_argument("--no-release-mode", action="store_true", help="Skip MotionSwitcher release")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--udp-target", type=str, default="", help="Target IP for UDP state")
    parser.add_argument("--udp-port", type=int, default=5562)
    parser.add_argument("--no-udp", action="store_true")
    args = parser.parse_args()
    if args.lowcmd_udp_port < 0 or args.lowcmd_udp_port > 65535:
        parser.error("--lowcmd-udp-port must be 0 or within [1, 65535]")

    config = BridgeConfig(
        state_port=args.state_port,
        cmd_port=args.cmd_port,
        udp_state_port=args.udp_port,
        lowcmd_udp_port=args.lowcmd_udp_port,
        loop_rate=args.rate,
        enable_dex3=not args.no_dex3,
        enable_brainco=not args.no_brainco,
        verbose=args.verbose,
        enable_udp_state=not args.no_udp and bool(args.udp_target),
        udp_target_ip=args.udp_target,
        publish_lowcmd=not args.drop_lowcmd,
        lowcmd_topic=args.lowcmd_topic,
        lowcmd_timeout_s=args.lowcmd_timeout_ms / 1000.0,
        lowcmd_failsafe_ramp_s=args.lowcmd_failsafe_ramp_ms / 1000.0,
        lowcmd_recovery_packets=args.lowcmd_recovery_packets,
        lowcmd_recovery_ramp_s=args.lowcmd_recovery_ramp_ms / 1000.0,
    )

    if args.drop_lowcmd:
        print("[Bridge] DIAGNOSTIC: /lowcmd publishing disabled")
    elif args.lowcmd_topic != "/lowcmd":
        print(f"[Bridge] DIAGNOSTIC: LowCmd redirected to {args.lowcmd_topic}")

    bridge = ZmqRos2Bridge(config)

    def signal_handler(sig, frame):
        print("\n[Bridge] Received shutdown signal")
        bridge.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not bridge.init_zmq():
        print("[Bridge] Failed to initialize ZMQ")
        sys.exit(1)
    if not bridge.init_lowcmd_udp():
        print("[Bridge] Failed to initialize authoritative LowCmd UDP receiver")
        bridge.cleanup()
        sys.exit(1)
    # Release MotionSwitcher BEFORE init_ros2: rclpy locks CycloneDDS domain 0,
    # after which unitree_sdk2py.ChannelFactoryInitialize fails. When invoked via
    # robot_services.sh, --no-release-mode is set and release is done in a
    # short-lived subprocess instead (so the domain is freed before rclpy starts).
    if not args.no_release_mode:
        print("[Bridge] Checking and releasing control mode...")
        _release_motion_switcher()
    if not bridge.init_ros2():
        print("[Bridge] Failed to initialize ROS2")
        bridge.cleanup()
        sys.exit(1)
    if config.enable_udp_state and config.udp_target_ip:
        if not bridge.init_udp(config.udp_target_ip):
            print("[Bridge] Warning: UDP init failed, continuing without UDP")
            config.enable_udp_state = False
        else:
            print(
                f"[Bridge] UDP state transmission enabled to "
                f"{config.udp_target_ip}:{config.udp_state_port}"
            )

    try:
        bridge.run()
    finally:
        bridge.cleanup()
        print("[Bridge] Shutdown complete")


if __name__ == "__main__":
    main()
