"""OM reference-plan v2 HTTP adapter. No token streaming or background renewals."""
from copy import deepcopy
import json
import logging
from collections import deque
from pathlib import Path
import secrets
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .replay import validate_plan, summary, plan_digest

ROUTE = '/api/execution/v2/plans'
ACTIVE = ('loading', 'starting', 'running', 'stopping')
TERMINAL = ('completed', 'stopped', 'failed')
BOUNDARY = 'protected_writer_dds_publish_returned_not_motor_execution'
STOP_BOUNDARY = 'owned_wbc_exited_and_protected_writer_terminal_not_contact_verified'


class Rejected(ValueError):
    pass


class CommandUnconfirmed(OSError):
    """The mutation was submitted, but its transport response was lost."""


class NativeReplayClient:
    def __init__(self, gateway, token):
        u = urlsplit(gateway)
        if (u.scheme != 'http' or u.hostname != '127.0.0.1' or not u.port or u.path or
                u.query or u.fragment or u.username or u.password):
            raise ValueError('Replay gateway must be local http://127.0.0.1:PORT')
        if not isinstance(token, str) or len(token) < 32: raise ValueError('Invalid gateway token')
        self.gateway, self.token = gateway, token
        self.client = 'oml-reference-'+secrets.token_hex(12)
        self.seq = 0
        self.request_history = deque(maxlen=32)

    def call(self, path, payload=None, timeout=.2):
        raw = None if payload is None else json.dumps(payload, allow_nan=False, separators=(',', ':')).encode()
        req = Request(self.gateway+path, data=raw,
                      headers={'Authorization': 'Bearer '+self.token, 'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=timeout) as response: value = json.load(response)
        except HTTPError as exc:
            try: reason = json.loads(exc.read(8192)).get('error', '')
            except (ValueError, AttributeError): reason = ''
            raise Rejected('OM HTTP %s: %s' % (exc.code, reason or 'Request rejected')) from None
        if not isinstance(value, dict): raise ValueError('Invalid OM response')
        return value

    def controller(self): return self.call('/api/controller')
    def status(self, plan_id): return self.call(ROUTE+'/'+plan_id)
    def load(self, plan): return self.call(ROUTE, {'plan': plan}, timeout=30)

    def command(self, plan_id, action, run_id=None):
        if action not in ('start', 'stop'): raise ValueError('Unsupported OM replay action')
        began=time.monotonic();entry=dict(action=action,run_id=run_id,at=began)
        try:
            auth = self.call('/api/controller/ticket')  # Fresh credentials without rendering full status.
            entry['controller_ms']=(time.monotonic()-began)*1000
            self.seq += 1
            payload = dict(client=self.client, seq=self.seq, epoch=auth['epoch'], ticket=auth['ticket'])
            if action != 'start': payload['run_id'] = run_id
            try: result=self.call(ROUTE+'/'+plan_id+'/'+action, payload)
            except OSError as exc: raise CommandUnconfirmed(str(exc)) from exc
            entry['state']=result.get('state');entry['reason']=result.get('reason')
            return result
        except (OSError,ValueError,KeyError,TypeError) as exc:
            entry['error']=type(exc).__name__+': '+str(exc)
            raise
        finally:
            entry['elapsed_ms']=(time.monotonic()-began)*1000
            self.request_history.append(entry)
            if entry.get('error') or entry.get('reason')=='lease_expired':
                logging.getLogger(__name__).warning(json.dumps(dict(event='replay_request_failure',recent=list(self.request_history))))


def from_config(path):
    path = Path(path).resolve(); config = json.loads(path.read_text())
    token = (path.parent/Path(config['token_file']).expanduser()).resolve()
    if token.stat().st_mode & 0o077: raise ValueError('Gateway token file must be private')
    return NativeReplay(NativeReplayClient(config['gateway'], token.read_text().strip()))


class NativeReplay:
    """OM owns execution; browser sessions only submit explicit commands and read status."""
    def __init__(self, client):
        self.client = client; self.lock = threading.RLock()
        self.plan_id = None; self.run_id = None; self.plan_summary = None
        self.sources = []; self.episode = None; self.segment = None
        self.owner = None; self.attempted = False; self.pending_action = None
        self.available = False; self.connected = False; self.error = ''
        self.latest = None; self.previous_position = None
        self.controller_running = False; self.controller_checked = 0.

    def snapshot(self):
        with self.lock:
            s = deepcopy(self.latest)
            controller_fresh = bool(self.connected and self.controller_checked and time.monotonic()-self.controller_checked<=1.5)
            return dict(configured=True, available=self.available, connected=self.connected,
                        controller_running=bool(controller_fresh and self.controller_running),
                        controller_status=('running' if self.controller_running else 'not_ready') if controller_fresh else 'unknown',
                        pending_action=self.pending_action, error=self.error, status=s, episode=self.episode, segment=self.segment,
                        summary=self.plan_summary, owned=self.run_id is not None,
                        can_start=bool(self.connected and s and s['state']=='loaded' and self.plan_id==s.get('plan_id') and not self.attempted),
                        can_stop=bool(self.pending_action != 'stop' and self.run_id is not None and s and s.get('stop_ack') is not True and not (s.get('state')=='completed' and s.get('completion_ack') is True)),
                        task_lifetime='runtime_owned', browser_lease_required=False)

    def _accept(self, value):
        if value.get('task_lifetime')!='runtime_owned' or value.get('browser_lease_required') is not False:
            raise ValueError('OM runtime-owned replay is required')
        if (value.get('schema') != 'onboardmimic.execution.status' or type(value.get('schema_version')) is not int or value.get('schema_version') != 2
                or value.get('state') not in ('empty', 'loaded')+ACTIVE+TERMINAL
                or type(value.get('stop_ack')) is not bool
                or value.get('progress_boundary') != BOUNDARY or value.get('stop_boundary') != STOP_BOUNDARY):
            raise ValueError('Incompatible OM replay status')
        if type(value.get('run_id')) is not int: raise ValueError('Invalid OM run identity')
        if value['state'] in ACTIVE and value.get('model_validation') != 'matched_live':
            raise ValueError('Active replay has no live model match')
        if self.plan_id and value.get('plan_id') != self.plan_id: raise ValueError('OM replay plan changed')
        if self.run_id is not None and value.get('run_id') != self.run_id: raise ValueError('OM replay run changed')
        pos = value.get('source_frame_position')
        if pos is None and (self.previous_position is not None or value.get('source_frame_index') is not None or value.get('source_reference_id') is not None):
            raise ValueError('Actual progress evidence is inconsistent')
        if pos is not None and self.sources:
            if type(pos) is not int or not 0 <= pos < len(self.sources): raise ValueError('Invalid actual frame position')
            if self.previous_position is not None and pos < self.previous_position: raise ValueError('Actual progress moved backwards')
            index, reference = self.sources[pos]
            if (value.get('source_frame_index'), value.get('source_reference_id')) != (index, reference):
                raise ValueError('Actual progress does not match the loaded source')
            commit = value.get('output_commit')
            if not isinstance(commit, dict) or commit.get('boundary') != BOUNDARY:
                raise ValueError('Actual progress lacks protected output evidence')
        handoff=value.get('planner_handoff')
        planner_done=(value.get('completion_ack') is True and
            value.get('completion_boundary')=='live_planner_protected_output_confirmed' and
            isinstance(handoff,dict) and handoff.get('boundary')==BOUNDARY and
            isinstance(value.get('output_commit'),dict) and
            all(handoff.get(k)==value['output_commit'].get(k) for k in ('stream','generation')) and
            handoff.get('sequence',0)>value['output_commit'].get('sequence',0))
        if value['state']=='completed' and (self.sources and pos != len(self.sources)-1 and not (value.get('reason')=='operator_stop' and planner_done) or not (value['stop_ack'] or planner_done)):
            raise ValueError('Completion lacks final consumption and exit confirmation')
        if value['state'] in ACTIVE and (value['stop_ack'] or value.get('completion_ack')):raise ValueError('Active replay cannot have exit confirmation')
        if value['state'] in ('stopped','failed') and not value['stop_ack']:raise ValueError('Terminal replay lacks stop confirmation')
        if pos is not None: self.previous_position = pos
        # Deliberate allowlist: no controller credentials/owner are sent to the browser.
        keys = ('schema','schema_version','plan_id','run_id','state','model_validation','reason','detail','diagnostic_id','entry_phase','timing',
                'source_frame_position','source_frame_index','source_reference_id','output_commit',
                'completion_ack','completion_boundary','planner_handoff','progress_boundary','stop_ack','stop_boundary','task_lifetime','browser_lease_required')
        self.latest = {k:deepcopy(value[k]) for k in keys if k in value}
        self.connected = self.available = True; self.error = ''

    def _failure(self, exc):
        self.connected = False; self.error = str(exc)
        self.controller_running = False

    def poll(self):
        with self.lock:
            try:
                controller = self.client.controller()
                self.controller_running = bool(controller.get('state')=='active' and controller.get('backend')=='sonic' and controller.get('pids'))
                self.controller_checked = time.monotonic()
                execution = controller.get('execution')
                if not isinstance(execution, dict):
                    self.available = False
                    raise ValueError('OM reference replay is not enabled on this gateway')
                if self.plan_id:
                    # Controller includes the same execution status and owner snapshot.
                    value = execution
                    # A lost start response is reconciled only with actual owner evidence.
                    adopted_run = None
                    if self.attempted and self.run_id is None and controller.get('control_owner') == self.client.client:
                        run = value.get('run_id')
                        if type(run) is int and run > 0 and value.get('state') in ACTIVE+TERMINAL: adopted_run = run
                    self._accept(value)
                    if adopted_run is not None: self.run_id = adopted_run
                    if self.run_id is not None:
                        if self.pending_action == 'start' and value['state'] in ACTIVE+TERMINAL:
                            self.pending_action = None
                        elif self.pending_action == 'stop' and (value['state'] in ('stopping',)+TERMINAL or value.get('entry_phase') in ('returning','settling_planner')):
                            self.pending_action = None
                else:
                    self._accept(execution)
            except (OSError, ValueError, KeyError, TypeError) as exc:self._failure(exc)
            return self.snapshot()

    def load(self, plan, episode, segment):
        validate_plan(plan)
        spec = plan['controller_spec']
        if plan.get('schema_version') != 2 or spec.get('test_fixture') is not False or spec.get('physical_source') is not True:
            raise ValueError('Physical replay requires a real native v2 recording; fixtures are refused')
        if not 2 <= len(plan['frames']): raise ValueError('OM requires at least 2 frames')
        with self.lock:
            # latest is accepted only after validating final consumption and the
            # protected Planner handoff. Normal completion leaves WBC running.
            finished=bool(self.latest and self.latest['state'] in TERMINAL and
                (self.latest.get('stop_ack') is True or
                 (self.latest['state']=='completed' and self.latest.get('completion_ack') is True)))
            if (self.latest and self.latest['state'] in ACTIVE) or (self.attempted and not finished):
                raise ValueError('Wait for confirmed stop or Planner handoff before loading')
            self.plan_id = plan_digest(plan); self.run_id = None; self.attempted = False
            self.owner = None; self.previous_position = None; self.pending_action = None
            self.plan_summary = summary(plan); self.episode = episode; self.segment = segment
            self.sources = [(f['sample']['index'],'parquet:'+plan['source_sha256']+':'+str(f['sample']['index'])) for f in plan['frames']]
            try:self._accept(self.client.load(plan))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._failure(exc);raise
            return self.snapshot()

    def command(self, action, owner=None, expected_plan=None, expected_run=None):
        with self.lock:
            if action not in ('start','stop'): raise ValueError('Unsupported replay command')
            if not self.plan_id or expected_plan != self.plan_id: raise ValueError('Loaded replay plan changed; reload status')
            if self.pending_action: raise ValueError('Command result is pending; query status before another command')
            if action == 'start':
                if not self.snapshot()['can_start']: raise ValueError('Load a plan and wait for OM status before starting')
                if not isinstance(owner,str) or not 16 <= len(owner) <= 128: raise ValueError('Invalid browser owner')
                self.owner = owner; self.attempted = True
            else:
                if self.run_id is None: raise ValueError('Run ownership is unknown; query status, do not retry start')
                if type(expected_run) is not int or expected_run != self.run_id: raise ValueError('Replay run changed; query status')
            try:
                value = self.client.command(self.plan_id, action, self.run_id)
                if action == 'start':
                    run = value.get('run_id')
                    if type(run) is not int or run <= 0: raise ValueError('OM did not identify the started run')
                    self.run_id = run
                self._accept(value)
            except CommandUnconfirmed:
                # Submission is not execution evidence. Only validated polling
                # may resolve this; never repeat the mutation automatically.
                self.pending_action = action
                self.connected = False; self.error = ''
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._failure(exc)
                if action == 'start' and isinstance(exc, Rejected):
                    self.attempted = False; self.owner = None
                # No automatic start/stop retries. OM's independent watchdog still applies.
                raise
            return self.snapshot()
