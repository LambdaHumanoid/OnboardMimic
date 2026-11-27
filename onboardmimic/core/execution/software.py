"""Software-only joint controller. No SDK, process launcher, or motor transport."""
import hashlib
import json
import math
import secrets
import threading
import time
from copy import deepcopy
from pathlib import Path

from . import ExecutionAdapter
from ..protection.fence import ReferenceFence
from ..protection.monitor import OutputEvidence, ProtectionMonitor


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


class SoftwareJoint(ExecutionAdapter):
    def __init__(self, config=None, clock_ns=time.monotonic_ns, realtime_ns=time.time_ns):
        self.config = deepcopy(config or dict(joint_names=['hip', 'knee', 'ankle'],
                           period_ns=20000000, max_gap_ns=60000000,
                           watchdog_ns=200000000, speed_rad_s=1., limit_rad=2.))
        c = self.config
        names = c['joint_names']
        if (not isinstance(names, list) or not 1 <= len(names) <= 64 or
                any(not isinstance(n, str) or not n.strip() for n in names) or len(set(names)) != len(names)):
            raise ValueError('Invalid joint ordering')
        for key in ('period_ns', 'max_gap_ns', 'watchdog_ns'):
            if type(c[key]) is not int or c[key] <= 0: raise ValueError(key)
        if not c['period_ns'] <= c['max_gap_ns'] < c['watchdog_ns']: raise ValueError('Invalid timing')
        for key in ('speed_rad_s', 'limit_rad'):
            if type(c[key]) not in (float, int) or not math.isfinite(c[key]) or c[key] <= 0: raise ValueError(key)
        self.clock_ns, self.realtime_ns = clock_ns, realtime_ns
        self.lock = threading.RLock()
        self.fence = ReferenceFence()
        self.monitor = ProtectionMonitor(lambda: self.clock_ns()/1e9, c['watchdog_ns']/1e9)
        self._spec = self._make_spec()
        self.contract = fingerprint(self._spec)
        self.session = None
        self.active = False
        self.reset()

    def _make_spec(self):
        c = self.config
        field = dict(shape=[len(c['joint_names'])], unit='rad', frame='joint_local',
                     semantic='joint_position_target', joint_names=c['joint_names'])
        state = dict(field, shape=[1, len(c['joint_names'])], semantic='joint_position_history')
        paths = [Path(__file__), Path(__file__).with_name('__init__.py'),
                 Path(__file__).with_name('server.py'),
                 Path(__file__).parents[1]/'gateway.py',
                 Path(__file__).parents[1]/'protection/fence.py',
                 Path(__file__).parents[1]/'protection/monitor.py',
                 Path(__file__).parents[1]/'protection/contracts.py']
        artifacts = {str(p.relative_to(Path(__file__).parents[1])): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        artifacts['loaded_config'] = fingerprint(c)
        return dict(schema='onboardmimic.lab.controller-spec', schema_version=1,
            robot_id='software_joint_fixture', backend_id='software_joint',
            execution_id='software_joint.v1', reference_space='software_joint.position.v1',
            clock='same_host_monotonic_ns', period_ns=c['period_ns'], max_gap_ns=c['max_gap_ns'],
            artifacts=artifacts, state={'q': state}, reference={'q_target': field},
            control_output={'q_target': deepcopy(field)},
            history=dict(length=1, period_ns=c['period_ns'], order='oldest_first', reset='required',
                         layout='state.q[0] is the actual software plant input at this tick'),
            record_modes=['teleoperation', 'model', 'replay'], hardware=False)

    def controller_spec(self): return deepcopy(self._spec)

    def reset(self):
        with self.lock:
            if self.active: raise ValueError('Stop before reset')
            self.session = secrets.token_hex(16)
            self.authority = self.fence.reset_for_session(self.session)
            self.monitor.begin(self.session, 'software_joint')
            self.q = [0.] * len(self.config['joint_names'])
            self.owner = self.mode = self.pending = self.last = None
            self.index = 0
            self.reason = ''
            self.last_tick = self.clock_ns()
            return self.session

    def start(self, owner, mode):
        with self.lock:
            if self.active or self.reason: raise ValueError('Reset required before start')
            if not isinstance(owner, str) or not 1 <= len(owner) <= 80: raise ValueError('Invalid owner')
            if mode not in self._spec['record_modes']: raise ValueError('Unsupported mode')
            self.owner, self.mode, self.active = owner, mode, True
            self.deadline = self.clock_ns() + self.config['watchdog_ns']
            self.last_tick = self.clock_ns()
            self.monitor.activate()

    def submit(self, owner, session, contract, reference_id, reference):
        with self.lock:
            self.watchdog()
            if not self.active or owner != self.owner or session != self.session or contract != self.contract:
                raise ValueError('Inactive, wrong owner, session, or controller contract')
            if not isinstance(reference_id, str) or not 1 <= len(reference_id) <= 128: raise ValueError('Invalid reference id')
            if not isinstance(reference, dict) or set(reference) != {'q_target'}: raise ValueError('Wrong reference fields')
            q = reference['q_target']
            if not isinstance(q, list) or len(q) != len(self.q) or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > self.config['limit_rad'] for v in q):
                raise ValueError('Invalid joint reference')
            if self.pending is not None: raise ValueError('Queue full: consume before submitting again')
            self.pending = (reference_id, deepcopy(reference))
            self.deadline = self.clock_ns() + self.config['watchdog_ns']

    def watchdog(self):
        with self.lock:
            if self.active and (self.clock_ns() >= self.deadline or
                    self.clock_ns()-self.last_tick >= self.config['watchdog_ns']):
                self.stop('execution_timeout')

    def step(self):
        with self.lock:
            self.watchdog()
            if not self.active: return None
            now = self.clock_ns()
            if now <= self.last_tick: return None
            dt = min(now-self.last_tick, self.config['period_ns'])/1e9
            self.last_tick = now
            if self.pending is None: return None
            reference_id, reference = self.pending
            state = list(self.q)
            delta = self.config['speed_rad_s'] * dt
            output = [q + max(-delta, min(delta, target-q)) for q, target in zip(state, reference['q_target'])]
            def commit(): self.q = output
            self.fence.submit(self.authority, commit)
            self.pending = None
            sample = dict(contract_sha256=self.contract, session_id=self.session, index=self.index,
                timestamp_monotonic_ns=now, timestamp_realtime_ns=self.realtime_ns(), state_history_ns=[now],
                state={'q': [state]}, reference_id=reference_id, reference_evidence='consumed',
                reference=reference, control_output={'q_target': list(output)})
            self.index += 1
            self.last = sample
            self.monitor.output(OutputEvidence(self.session, sample['index'], now/1e9))
            return deepcopy(sample)

    def stop(self, reason='operator_stop'):
        with self.lock:
            if self.reason: return
            if reason != 'operator_stop': self.monitor.fault(reason, True)
            else: self.monitor.stop()
            # Every software write uses this fence. Revocation is synchronous;
            # fixture protection is a hold, never a claimed physical rollout.
            protection = self.fence.revoke_and_claim(self.authority)
            self.fence.submit(protection, lambda: None)
            self.fence.finish(protection)
            self.active = False
            self.pending = None
            self.owner = None
            self.reason = reason

    def status(self):
        with self.lock:
            self.watchdog()
            fresh = self.last is not None and 0 <= self.clock_ns()-self.last['timestamp_monotonic_ns'] <= self.config['max_gap_ns']
            return dict(state='active' if self.active else 'stopped', input_mode=self.mode,
                mode_switch_pending=False, hardware=False, stop_ack=bool(self.reason),
                stop_reason=self.reason, consumed_index=None if self.last is None else self.last['index'],
                consumed_reference_id=None if self.last is None else self.last['reference_id'],
                lab=dict(record_ready=bool(self.active and self.owner and fresh),
                         contract_sha256=self.contract, session_id=self.session),
                protection=dict(policy='software_hold', authority_transfer='reference_fence',
                                hardware_validated=False, monitor=self.monitor.snapshot()))
