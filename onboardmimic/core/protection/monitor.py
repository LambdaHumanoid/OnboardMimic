"""No robot SDK, transport, process mutation or motor-command output.

Output evidence is trusted adapter data from newly accepted WBC commands, never
planner/browser heartbeats. Receipt times share the runtime monotonic clock.
"""
from dataclasses import dataclass
import math
import threading

@dataclass(frozen=True)
class OutputEvidence:
    session: str
    sequence: int
    received_at: float

class ProtectionMonitor:
    def __init__(self, clock, output_timeout=.1, event_sink=None):
        if not math.isfinite(output_timeout) or output_timeout <= 0:
            raise ValueError('Invalid output timeout')
        self.clock=clock;self.output_timeout=output_timeout;self.event_sink=event_sink
        self.lock=threading.RLock();self.session=None;self.backend=None
        self.state='disarmed';self.event=None;self.last_output=None;self.sequence=-1
        self.generation=0;self.event_count=0;self.last_now=None

    def _now(self):
        now=self.clock()
        if not math.isfinite(now) or (self.last_now is not None and now<self.last_now):
            raise ValueError('Invalid monotonic clock')
        self.last_now=now
        return now

    def begin(self, session, backend):
        with self.lock:
            self._now()
            if not session or session==self.session:
                raise ValueError('Explicit new WBC session required')
            self.session=session;self.backend=backend;self.generation+=1
            self.state='starting';self.event=None;self.sequence=-1;self.last_output=None

    def activate(self):
        with self.lock:
            if self.state=='starting':self.state='monitoring'

    def output(self, evidence):
        with self.lock:
            now=self._now()
            if self.state not in ('starting','monitoring') or evidence.session!=self.session:
                return False
            if (type(evidence.sequence) is not int or evidence.sequence<=self.sequence or
                not math.isfinite(evidence.received_at) or evidence.received_at>now or
                now-evidence.received_at>=self.output_timeout or
                (self.last_output is not None and evidence.received_at<=self.last_output)):
                return False
            self.sequence=evidence.sequence;self.last_output=evidence.received_at
            return True

    def stop(self, reason='operator_stop', estop=False):
        with self.lock:
            self._now()
            # A later runtime cleanup must not erase a fault/estop incident.
            if estop:self.state='estop'
            elif self.state not in ('latched','estop'):self.state='intentional_stop'
            if self.event is None:self.event={'reason':reason,'at':self.clock(),'kind':self.state}

    def fault(self, reason, feedback_usable):
        with self.lock:
            now=self._now()
            if self.state not in ('starting','monitoring'):return None
            starting=self.state=='starting'
            self.state='latched';self.event_count+=1
            self.event=dict(kind='startup_fault' if starting else ('shadow_request' if feedback_usable else 'blocked_state'),
                reason=reason,at=now,session=self.session,backend=self.backend,
                generation=self.generation,strategy='compact_backward_roll',
                output_age_s=None if self.last_output is None else now-self.last_output,
                actuation=False)
            if self.event_sink:
                # Diagnostics must never prevent existing halt/estop behavior.
                try:self.event_sink(dict(self.event))
                except Exception:pass
            return dict(self.event)

    def observe(self, producer_alive, feedback_usable):
        with self.lock:
            now=self._now()
            if self.state not in ('starting','monitoring'):return None
            if not producer_alive:return self.fault('wbc_process_exit',feedback_usable)
            if self.state=='monitoring' and self.last_output is not None and now-self.last_output>=self.output_timeout:
                return self.fault('wbc_output_timeout',feedback_usable)
            return None

    def snapshot(self):
        with self.lock:
            return dict(mode='shadow',automatic_execution=False,state=self.state,
                backend=self.backend,session=self.session,generation=self.generation,
                strategy='compact_backward_roll',strategy_validated_for_hardware=False,
                output_monitoring='sequence_verified' if self.last_output is not None else 'unavailable',
                output_timeout_s=self.output_timeout,event_count=self.event_count,
                last_event=dict(self.event) if self.event else None,
                authority_transfer='not_connected',runtime_crash_coverage=False)
