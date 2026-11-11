"""Contracts implemented by robot and S0 adapters (Python 3.8 compatible).

Adapters are trusted installed code. Constructing one must not connect to hardware,
start a service or send commands. A supported pair is selected before runtime starts.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List


@dataclass(frozen=True)
class LaunchSpec:
    argv: List[str]
    cwd: Path
    env: Dict[str, str]


@dataclass(frozen=True)
class BackendProgress:
    preparing: bool = False
    prepared: bool = False
    active: bool = False
    armed: bool = False


class RobotAdapter(ABC):
    id: str
    label: str
    joint_count: int
    state_topics: FrozenSet[str]

    @abstractmethod
    def telemetry(self, latest, target, now):
        """Decode hardware feedback into q/dq/faults/fresh/valid/upright fields."""

    @abstractmethod
    def safe(self, telemetry):
        """Robot-specific admission thresholds; never infer from model success."""

    @abstractmethod
    def guard(self, owned_pid=None):
        """Read-only live control-owner and transport readiness checks."""

    def ingest(self, topic, value, now):
        """Optional feedback-health messages (e.g. redundant receiver status)."""

    def feedback_ready(self, latest, now):
        return True

    def feedback_status(self, latest, now):
        return {}

    def service_units(self, root, config):
        raise ValueError("No service installation for "+self.id)

    def environment_template(self, root):
        return ""

    @abstractmethod
    def connection(self):
        """Return a console connection adapter; no side effects on construction."""


class S0Backend(ABC):
    id: str
    label: str
    compatible_robots: FrozenSet[str]
    capabilities: FrozenSet[str]
    prepare_seconds = 3
    handoff_seconds = 1
    prepare_timeout = 150
    handoff_timeout = 12
    max_speed = 5.0
    supported_inputs = frozenset()

    @abstractmethod
    def baseline(self, robot):
        """Reference joint positions in the selected robot's hardware ordering."""

    @abstractmethod
    def launch_spec(self, robot):
        """Prepare launch arguments; called only after explicit posture preparation."""

    @abstractmethod
    def progress(self, text):
        """Decode backend-specific progress into BackendProgress."""

    @abstractmethod
    def decode_status(self, topic, value):
        """Return normalized {mode, playing} or None for unrelated messages."""

    @abstractmethod
    def transport(self, gateway, robot):
        """Create the backend command/feedback transport, without starting it."""

    def installer(self, robot):
        """Return a pair-specific setup entrypoint, or reject before any mutation."""
        raise ValueError("No installer for "+robot.id+"/"+self.id)

    def pose_reader(self, input_id, path):
        raise ValueError('Backend does not support input: '+input_id)


class TrackingInput(ABC):
    id: str
    label: str

    def preview_script(self):
        """Optional trusted input-specific browser preview, without control actions."""
        return None

    @abstractmethod
    def status(self):
        """Receive-only overview, never robot admission or control authority."""

    @abstractmethod
    def skeleton(self):
        """Optional diagnostic skeleton; no retargeting or robot commands."""
