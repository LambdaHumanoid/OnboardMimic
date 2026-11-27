"""Offline decision prototype only. No robot transport or motor commands.

Adapter contract: last_output must advance only on a new validated SONIC command,
not on a browser heartbeat or repeated stale command. Call start_session once
with a new run identity; explicit stops must be recorded before stopping SONIC.
All times use the same monotonic clock. Thresholds are experimental, not certified.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class Observation:
    now: float
    last_output: float
    last_state: float
    producer_alive: bool
    estop: bool = False
    actuators_available: bool = True
    ownership_available: bool = True

class Detector:
    def __init__(self, output_timeout=.02, state_timeout=.02):
        if output_timeout <= 0 or state_timeout <= 0:
            raise ValueError('Timeouts must be positive')
        self.output_timeout = output_timeout
        self.state_timeout = state_timeout
        self.session = None
        self.active = False
        self.intentional = False
        self.latched = False
        self.last_now = float('-inf')

    def start_session(self, session, observed_running):
        if not session or not observed_running:
            raise ValueError('Require a new session with verified live SONIC output')
        if session == self.session:
            raise ValueError('A latched session cannot be silently rearmed')
        self.session = session
        self.active = True
        self.intentional = self.latched = False

    def intentional_stop(self, session):
        if session != self.session:
            raise ValueError('Stop intent must match current session')
        self.intentional = True

    def observe(self, o):
        import math
        if not all(math.isfinite(x) for x in (o.now, o.last_output, o.last_state)):
            raise ValueError('Nonfinite timestamp')
        if o.now < self.last_now or max(o.last_output, o.last_state) > o.now:
            raise ValueError('Invalid monotonic timestamps')
        self.last_now = o.now
        if o.estop:
            self.latched = True
            return 'ESTOP_PRIORITY'
        if not self.active or self.intentional:
            return 'DISARMED'
        if self.latched:
            return 'LATCHED'
        if o.producer_alive and o.now-o.last_output < self.output_timeout:
            return 'MONITORING'
        self.latched = True
        if o.now-o.last_state >= self.state_timeout:
            return 'BLOCKED_STATE_LOST'
        if not o.actuators_available or not o.ownership_available:
            return 'BLOCKED_CONTROL_UNAVAILABLE'
        # This is an audit event, deliberately not permission to execute a roll.
        return 'SHADOW_ROLL_REQUEST'
