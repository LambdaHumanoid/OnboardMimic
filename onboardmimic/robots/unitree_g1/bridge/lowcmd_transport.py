"""Pure transport diagnostics and safety state for the robot LowCmd stream.

This module deliberately has no ROS 2 or Unitree imports so its timing and
state-machine behavior can be unit tested on the host.  SONIC's primary
real-robot command path is one compact UDP datagram per LowCmd.  At 931 bytes it
stays below the normal Ethernet MTU, so a command cannot be partially delivered
or held behind an older TCP segment.

The same timing metadata is also carried as an optional third ZMQ multipart
frame:

    [topic, msgpack LowCmd payload, LowCmdTransportMetadata]

That ZMQ copy is a compatibility fallback for older bridges.  A UDP-capable
bridge ignores metadata-bearing ZMQ LowCmd frames, while continuing to accept
historical two-frame publishers such as HOMIE.
"""

from dataclasses import dataclass
from enum import Enum
import math
import struct
from typing import Optional, Tuple


LOWCMD_META_MAGIC = b"LCM1"
LOWCMD_META_VERSION = 1
LOWCMD_META_STRUCT = struct.Struct("<4sHHQQQ")

LOWCMD_UDP_MAGIC = b"LCU1"
LOWCMD_UDP_VERSION = 1
LOWCMD_UDP_MOTOR_COUNT = 35
# magic/version/flags + stream/sequence/send time + command modes/count +
# reserve[4] + Unitree CRC
LOWCMD_UDP_HEADER_STRUCT = struct.Struct("<4sHHQQQBBBB4II")
# mode + q/dq/tau/kp/kd + reserve
LOWCMD_UDP_MOTOR_STRUCT = struct.Struct("<B5fI")
LOWCMD_UDP_PACKET_SIZE = (
    LOWCMD_UDP_HEADER_STRUCT.size
    + LOWCMD_UDP_MOTOR_COUNT * LOWCMD_UDP_MOTOR_STRUCT.size
)
assert LOWCMD_UDP_PACKET_SIZE == 931


@dataclass(frozen=True)
class LowCmdTransportMetadata:
    """Host-side timing attached to one attempted LowCmd publication."""

    stream_id: int
    sequence: int
    send_steady_ns: int
    flags: int = 0
    version: int = LOWCMD_META_VERSION

    def encode(self) -> bytes:
        return LOWCMD_META_STRUCT.pack(
            LOWCMD_META_MAGIC,
            int(self.version),
            int(self.flags),
            int(self.stream_id),
            int(self.sequence),
            int(self.send_steady_ns),
        )

    @classmethod
    def decode(cls, payload: bytes) -> Optional["LowCmdTransportMetadata"]:
        if len(payload) != LOWCMD_META_STRUCT.size:
            return None
        magic, version, flags, stream_id, sequence, send_steady_ns = (
            LOWCMD_META_STRUCT.unpack(payload)
        )
        if magic != LOWCMD_META_MAGIC or version != LOWCMD_META_VERSION:
            return None
        return cls(
            stream_id=stream_id,
            sequence=sequence,
            send_steady_ns=send_steady_ns,
            flags=flags,
            version=version,
        )


@dataclass(frozen=True)
class LowCmdMotor:
    """One motor entry in the compact LowCmd UDP wire format."""

    mode: int
    q: float
    dq: float
    tau: float
    kp: float
    kd: float
    reserve: int = 0

    def is_finite(self) -> bool:
        return all(
            math.isfinite(value)
            for value in (self.q, self.dq, self.tau, self.kp, self.kd)
        )


@dataclass(frozen=True)
class LowCmdUdpPacket:
    """Fixed-size, single-datagram representation of a Unitree LowCmd."""

    metadata: LowCmdTransportMetadata
    mode_pr: int
    mode_machine: int
    motor_cmd: Tuple[LowCmdMotor, ...]
    reserve: Tuple[int, int, int, int]
    crc: int

    def encode(self) -> bytes:
        if len(self.motor_cmd) != LOWCMD_UDP_MOTOR_COUNT:
            raise ValueError(
                f"motor_cmd must contain {LOWCMD_UDP_MOTOR_COUNT} entries"
            )
        if len(self.reserve) != 4:
            raise ValueError("reserve must contain four entries")
        if not all(motor.is_finite() for motor in self.motor_cmd):
            raise ValueError("motor command contains non-finite values")

        output = bytearray(LOWCMD_UDP_PACKET_SIZE)
        LOWCMD_UDP_HEADER_STRUCT.pack_into(
            output,
            0,
            LOWCMD_UDP_MAGIC,
            LOWCMD_UDP_VERSION,
            int(self.metadata.flags),
            int(self.metadata.stream_id),
            int(self.metadata.sequence),
            int(self.metadata.send_steady_ns),
            int(self.mode_pr),
            int(self.mode_machine),
            LOWCMD_UDP_MOTOR_COUNT,
            0,
            *(int(value) for value in self.reserve),
            int(self.crc) & 0xFFFFFFFF,
        )
        offset = LOWCMD_UDP_HEADER_STRUCT.size
        for motor in self.motor_cmd:
            LOWCMD_UDP_MOTOR_STRUCT.pack_into(
                output,
                offset,
                int(motor.mode),
                float(motor.q),
                float(motor.dq),
                float(motor.tau),
                float(motor.kp),
                float(motor.kd),
                int(motor.reserve) & 0xFFFFFFFF,
            )
            offset += LOWCMD_UDP_MOTOR_STRUCT.size
        return bytes(output)

    @classmethod
    def decode_metadata(
        cls,
        payload: bytes,
    ) -> Optional[LowCmdTransportMetadata]:
        """Decode only the fixed header for low-allocation timing telemetry."""
        if len(payload) != LOWCMD_UDP_PACKET_SIZE:
            return None
        (
            magic,
            version,
            flags,
            stream_id,
            sequence,
            send_steady_ns,
            _mode_pr,
            _mode_machine,
            motor_count,
            _reserved,
            _reserve_0,
            _reserve_1,
            _reserve_2,
            _reserve_3,
            _crc,
        ) = LOWCMD_UDP_HEADER_STRUCT.unpack_from(payload, 0)
        if (
            magic != LOWCMD_UDP_MAGIC
            or version != LOWCMD_UDP_VERSION
            or motor_count != LOWCMD_UDP_MOTOR_COUNT
        ):
            return None
        return LowCmdTransportMetadata(
            stream_id=stream_id,
            sequence=sequence,
            send_steady_ns=send_steady_ns,
            flags=flags,
        )

    @classmethod
    def decode(cls, payload: bytes) -> Optional["LowCmdUdpPacket"]:
        if len(payload) != LOWCMD_UDP_PACKET_SIZE:
            return None
        (
            magic,
            version,
            flags,
            stream_id,
            sequence,
            send_steady_ns,
            mode_pr,
            mode_machine,
            motor_count,
            _reserved,
            reserve_0,
            reserve_1,
            reserve_2,
            reserve_3,
            crc,
        ) = LOWCMD_UDP_HEADER_STRUCT.unpack_from(payload, 0)
        if (
            magic != LOWCMD_UDP_MAGIC
            or version != LOWCMD_UDP_VERSION
            or motor_count != LOWCMD_UDP_MOTOR_COUNT
        ):
            return None

        motors = []
        offset = LOWCMD_UDP_HEADER_STRUCT.size
        for _ in range(LOWCMD_UDP_MOTOR_COUNT):
            mode, q, dq, tau, kp, kd, reserve = (
                LOWCMD_UDP_MOTOR_STRUCT.unpack_from(payload, offset)
            )
            motor = LowCmdMotor(mode, q, dq, tau, kp, kd, reserve)
            if not motor.is_finite():
                return None
            motors.append(motor)
            offset += LOWCMD_UDP_MOTOR_STRUCT.size

        return cls(
            metadata=LowCmdTransportMetadata(
                stream_id=stream_id,
                sequence=sequence,
                send_steady_ns=send_steady_ns,
                flags=flags,
            ),
            mode_pr=mode_pr,
            mode_machine=mode_machine,
            motor_cmd=tuple(motors),
            reserve=(reserve_0, reserve_1, reserve_2, reserve_3),
            crc=crc,
        )


class LowCmdTransportMonitor:
    """Classify sender stalls separately from delivery stalls.

    Absolute monotonic clocks differ across machines.  Comparing *changes* in
    host send time with changes in robot receive time removes that fixed offset:

        transport_excess = receive_gap - sender_gap

    A large sender gap points at the command-writer thread.  A small sender gap
    plus a large positive excess points at ZMQ/TCP/Wi-Fi delivery.
    """

    def __init__(self) -> None:
        self.gap_thresholds_ms = (20.0, 50.0, 100.0)
        self.metadata_frames = 0
        self.legacy_frames = 0
        self.sequence_drops = 0
        self.out_of_order = 0
        self.stream_changes = 0
        self.max_receive_gap_ms = 0.0
        self.max_sender_gap_ms = 0.0
        self.max_transport_excess_ms = 0.0
        self.receive_gap_counts = {
            threshold: 0 for threshold in self.gap_thresholds_ms
        }
        self.sender_gap_counts = {
            threshold: 0 for threshold in self.gap_thresholds_ms
        }
        self.transport_excess_counts = {
            threshold: 0 for threshold in self.gap_thresholds_ms
        }
        self._previous_stream_id: Optional[int] = None
        self._previous_sequence: Optional[int] = None
        self._previous_send_ns: Optional[int] = None
        self._previous_receive_ns: Optional[int] = None

    def observe(
        self,
        metadata: Optional[LowCmdTransportMetadata],
        receive_steady_ns: int,
    ) -> None:
        if metadata is None:
            self.legacy_frames += 1
            return

        self.metadata_frames += 1
        if (
            self._previous_stream_id is not None
            and metadata.stream_id != self._previous_stream_id
        ):
            self.stream_changes += 1
            self._previous_sequence = None
            self._previous_send_ns = None
            self._previous_receive_ns = None

        if (
            self._previous_sequence is not None
            and metadata.stream_id == self._previous_stream_id
        ):
            sequence_delta = metadata.sequence - self._previous_sequence
            if sequence_delta > 1:
                self.sequence_drops += sequence_delta - 1
            elif sequence_delta <= 0:
                self.out_of_order += 1
                # Preserve the newest accepted baseline.  Moving it backward
                # would make the next in-order packet look like an additional
                # artificial sequence gap.
                return

        if (
            self._previous_send_ns is not None
            and self._previous_receive_ns is not None
            and metadata.stream_id == self._previous_stream_id
        ):
            sender_gap_ms = (
                metadata.send_steady_ns - self._previous_send_ns
            ) / 1e6
            receive_gap_ms = (
                receive_steady_ns - self._previous_receive_ns
            ) / 1e6
            if sender_gap_ms >= 0.0 and receive_gap_ms >= 0.0:
                self.max_sender_gap_ms = max(
                    self.max_sender_gap_ms, sender_gap_ms
                )
                self.max_receive_gap_ms = max(
                    self.max_receive_gap_ms, receive_gap_ms
                )
                self.max_transport_excess_ms = max(
                    self.max_transport_excess_ms,
                    receive_gap_ms - sender_gap_ms,
                )
                transport_excess_ms = max(0.0, receive_gap_ms - sender_gap_ms)
                for threshold in self.gap_thresholds_ms:
                    if receive_gap_ms > threshold:
                        self.receive_gap_counts[threshold] += 1
                    if sender_gap_ms > threshold:
                        self.sender_gap_counts[threshold] += 1
                    if transport_excess_ms > threshold:
                        self.transport_excess_counts[threshold] += 1

        self._previous_stream_id = metadata.stream_id
        self._previous_sequence = metadata.sequence
        self._previous_send_ns = metadata.send_steady_ns
        self._previous_receive_ns = receive_steady_ns

    def summary(self) -> str:
        threshold_summary = "/".join(
            str(int(threshold)) for threshold in self.gap_thresholds_ms
        )
        receive_counts = "/".join(
            str(self.receive_gap_counts[threshold])
            for threshold in self.gap_thresholds_ms
        )
        sender_counts = "/".join(
            str(self.sender_gap_counts[threshold])
            for threshold in self.gap_thresholds_ms
        )
        transport_counts = "/".join(
            str(self.transport_excess_counts[threshold])
            for threshold in self.gap_thresholds_ms
        )
        return (
            f"meta={self.metadata_frames} legacy={self.legacy_frames} "
            f"seq_drop={self.sequence_drops} reorder={self.out_of_order} "
            f"stream_change={self.stream_changes} "
            f"max_gap_ms(rx={self.max_receive_gap_ms:.1f},"
            f"sender={self.max_sender_gap_ms:.1f},"
            f"transport={self.max_transport_excess_ms:.1f}) "
            f"gap>{threshold_summary}ms("
            f"rx={receive_counts},sender={sender_counts},"
            f"transport={transport_counts})"
        )


class WatchdogState(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    FAILING = "failing"
    LATCHED = "latched"
    RECOVERING = "recovering"


class CommandDisposition(str, Enum):
    APPLY = "apply"
    HOLD = "hold"
    APPLY_RECOVERY = "apply_recovery"


@dataclass(frozen=True)
class WatchdogDecision:
    disposition: CommandDisposition
    state: WatchdogState
    event: Optional[str] = None


class LowCmdWatchdog:
    """Latched LowCmd watchdog with explicit new-stream recovery.

    A stale active stream starts a gain ramp toward damping and is then latched.
    Packets from that same stream can never auto-recover the robot.  Recovery
    requires a new publisher stream plus a run of consecutive packets; bridge
    code then blends from measured-position damping into that new stream.
    """

    def __init__(
        self,
        timeout_s: float = 0.1,
        failsafe_ramp_s: float = 0.15,
        recovery_packets: int = 25,
        recovery_ramp_s: float = 0.5,
        recovery_packet_max_gap_s: float = 0.05,
    ) -> None:
        if timeout_s < 0.0:
            raise ValueError("timeout_s must be non-negative")
        if failsafe_ramp_s < 0.0:
            raise ValueError("failsafe_ramp_s must be non-negative")
        if recovery_packets < 1:
            raise ValueError("recovery_packets must be positive")
        if recovery_ramp_s < 0.0:
            raise ValueError("recovery_ramp_s must be non-negative")
        if recovery_packet_max_gap_s <= 0.0:
            raise ValueError(
                "recovery_packet_max_gap_s must be positive"
            )

        self.timeout_s = timeout_s
        self.failsafe_ramp_s = failsafe_ramp_s
        self.recovery_packets = recovery_packets
        self.recovery_ramp_s = recovery_ramp_s
        self.recovery_packet_max_gap_s = recovery_packet_max_gap_s

        self.state = WatchdogState.IDLE
        self.active_stream_id: Optional[int] = None
        self.last_active_sequence: Optional[int] = None
        self.last_active_receive_s: Optional[float] = None
        self.fail_started_s: Optional[float] = None
        self.recovery_started_s: Optional[float] = None

        self._candidate_stream_id: Optional[int] = None
        self._candidate_last_sequence: Optional[int] = None
        self._candidate_last_receive_s: Optional[float] = None
        self._candidate_packets = 0

    @staticmethod
    def _stream_id(
        metadata: Optional[LowCmdTransportMetadata],
    ) -> Optional[int]:
        return None if metadata is None else metadata.stream_id

    @staticmethod
    def _sequence(
        metadata: Optional[LowCmdTransportMetadata],
    ) -> Optional[int]:
        return None if metadata is None else metadata.sequence

    def advance(self, now_s: float) -> Optional[str]:
        """Advance timeout/ramp state and return a transition event, if any."""
        if (
            self.state in (WatchdogState.ACTIVE, WatchdogState.RECOVERING)
            and self.timeout_s > 0.0
            and self.last_active_receive_s is not None
            and now_s - self.last_active_receive_s > self.timeout_s
        ):
            previous = self.state
            self.state = WatchdogState.FAILING
            self.fail_started_s = now_s
            self.recovery_started_s = None
            self._clear_candidate()
            return f"stale:{previous.value}"

        if self.state == WatchdogState.FAILING:
            started_s = (
                now_s if self.fail_started_s is None else self.fail_started_s
            )
            elapsed = now_s - started_s
            if self.failsafe_ramp_s == 0.0 or elapsed >= self.failsafe_ramp_s:
                self.state = WatchdogState.LATCHED
                return "failsafe_latched"
        return None

    def observe(
        self,
        metadata: Optional[LowCmdTransportMetadata],
        now_s: float,
    ) -> WatchdogDecision:
        event = self.advance(now_s)
        stream_id = self._stream_id(metadata)
        sequence = self._sequence(metadata)

        if self.state == WatchdogState.IDLE:
            self.state = WatchdogState.ACTIVE
            self.active_stream_id = stream_id
            self.last_active_sequence = sequence
            self.last_active_receive_s = now_s
            return WatchdogDecision(
                CommandDisposition.APPLY,
                self.state,
                event or "stream_armed",
            )

        if self.state == WatchdogState.ACTIVE:
            if stream_id != self.active_stream_id:
                return WatchdogDecision(
                    CommandDisposition.HOLD,
                    self.state,
                    event or "foreign_stream_ignored",
                )
            if (
                sequence is not None
                and self.last_active_sequence is not None
                and sequence <= self.last_active_sequence
            ):
                return WatchdogDecision(
                    CommandDisposition.HOLD,
                    self.state,
                    event or "non_monotonic_sequence",
                )
            self.last_active_sequence = sequence
            self.last_active_receive_s = now_s
            return WatchdogDecision(
                CommandDisposition.APPLY,
                self.state,
                event,
            )

        if self.state == WatchdogState.RECOVERING:
            if stream_id != self.active_stream_id:
                return WatchdogDecision(
                    CommandDisposition.HOLD,
                    self.state,
                    event or "foreign_stream_ignored",
                )
            if (
                sequence is not None
                and self.last_active_sequence is not None
                and sequence <= self.last_active_sequence
            ):
                return WatchdogDecision(
                    CommandDisposition.HOLD,
                    self.state,
                    event or "non_monotonic_sequence",
                )
            self.last_active_sequence = sequence
            self.last_active_receive_s = now_s
            if self.recovery_alpha(now_s) >= 1.0:
                self.state = WatchdogState.ACTIVE
                self.recovery_started_s = None
                return WatchdogDecision(
                    CommandDisposition.APPLY,
                    self.state,
                    event or "recovery_complete",
                )
            return WatchdogDecision(
                CommandDisposition.APPLY_RECOVERY,
                self.state,
                event,
            )

        # Once stale, the old stream is never allowed to auto-recover.  A
        # metadata-less publisher likewise cannot prove a process restart.
        if (
            self.state == WatchdogState.FAILING
            or stream_id is None
            or stream_id == self.active_stream_id
        ):
            return WatchdogDecision(
                CommandDisposition.HOLD,
                self.state,
                event,
            )

        if self._candidate_stream_id != stream_id:
            self._candidate_stream_id = stream_id
            self._candidate_last_sequence = sequence
            self._candidate_last_receive_s = now_s
            self._candidate_packets = 1
        elif (
            sequence is not None
            and self._candidate_last_sequence is not None
            and sequence > self._candidate_last_sequence
            and self._candidate_last_receive_s is not None
            and now_s - self._candidate_last_receive_s
            <= self.recovery_packet_max_gap_s
        ):
            self._candidate_last_sequence = sequence
            self._candidate_last_receive_s = now_s
            self._candidate_packets += 1
        else:
            self._candidate_last_sequence = sequence
            self._candidate_last_receive_s = now_s
            self._candidate_packets = 1

        if self._candidate_packets < self.recovery_packets:
            return WatchdogDecision(
                CommandDisposition.HOLD,
                self.state,
                "new_stream_validation",
            )

        self.active_stream_id = stream_id
        self.last_active_sequence = sequence
        self.last_active_receive_s = now_s
        self.recovery_started_s = now_s
        self.state = WatchdogState.RECOVERING
        self._clear_candidate()
        return WatchdogDecision(
            CommandDisposition.APPLY_RECOVERY,
            self.state,
            "new_stream_recovery",
        )

    def failsafe_alpha(self, now_s: float) -> float:
        if self.state == WatchdogState.LATCHED:
            return 1.0
        if self.state != WatchdogState.FAILING:
            return 0.0
        if self.failsafe_ramp_s == 0.0:
            return 1.0
        started_s = (
            now_s if self.fail_started_s is None else self.fail_started_s
        )
        return min(
            1.0,
            max(0.0, (now_s - started_s) / self.failsafe_ramp_s),
        )

    def recovery_alpha(self, now_s: float) -> float:
        if self.state == WatchdogState.ACTIVE:
            return 1.0
        if self.state != WatchdogState.RECOVERING:
            return 0.0
        if self.recovery_ramp_s == 0.0:
            return 1.0
        started_s = (
            now_s
            if self.recovery_started_s is None
            else self.recovery_started_s
        )
        return min(
            1.0,
            max(
                0.0,
                (now_s - started_s) / self.recovery_ramp_s,
            ),
        )

    def _clear_candidate(self) -> None:
        self._candidate_stream_id = None
        self._candidate_last_sequence = None
        self._candidate_last_receive_s = None
        self._candidate_packets = 0
