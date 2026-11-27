"""SONIC-only physical replay adapter. HTTP cannot write motor commands."""
import json
import logging
import os
import secrets
import socket
import struct
import threading
import time
from copy import deepcopy
from .replay_plan import validate, compatible
from onboardmimic.core.execution.software import fingerprint


class NativeClient:
    def __init__(self, path, pid): self.path, self.pid = str(path), pid

    def request(self, action, **fields):
        data=json.dumps(dict(action=action,**fields),allow_nan=False,separators=(',',':')).encode()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(5. if action=='load' else .2);sock.connect(self.path)
            pid,uid,_=struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid!=os.getuid() or pid!=self.pid: raise RuntimeError('Replay native peer identity mismatch')
            sock.sendall(struct.pack('=Q',len(data))+data)
            def read(n):
                out=b''
                while len(out)<n:
                    chunk=sock.recv(n-len(out))
                    if not chunk: raise RuntimeError('Replay native closed without confirmation')
                    out+=chunk
                return out
            size=struct.unpack('=Q',read(8))[0]
            if not 0<size<=8192: raise RuntimeError('Invalid native response')
            result=json.loads(read(size))
            if result.get('ok') is not True: raise RuntimeError('Replay native request rejected')
            return result


class Replay:
    def __init__(self, gateway, path, client_factory=NativeClient, clock=time.monotonic, start_worker=True):
        from .replay_loader import PlanLoader
        self.loader=PlanLoader()
        self.g=gateway;self.path=path;self.client_factory=client_factory;self.clock=clock
        self.diagnostic_id=None;self.native_status=None;self.operator_return=False;self.admission_failure=None
        self.detail='';self.model_verified=False;self.plan=None;self.state='empty';self.reason='';self.owner=None;self.run=0;self.client=None
        self.native_seq=0;self.frame=None;self.commit=None;self.run_started=0;self.binding=None
        self.entry_phase=None;self.returning=False;self.handoff=None
        self.closed=threading.Event();self.thread=threading.Thread(target=self._work,daemon=True)
        if start_worker:self.thread.start()

    @property
    def busy(self): return self.state in ('loading','starting','running','stopping')

    def live(self, *, include_sample=True):
        receiver=getattr(self.g,'native_consumption',None)
        if not receiver: raise ValueError('Native telemetry not enabled')
        with receiver.lock:
            if receiver.error or receiver.stop.is_set() or receiver.join.spec is None: raise ValueError('Live native spec unavailable')
            return deepcopy(receiver.join.spec),deepcopy(receiver.join.last) if include_sample else None

    def load(self, value):
        # Potentially large validation never holds the motor gateway lock.
        plan=validate(value)
        return self._install(plan)

    def load_bytes(self, payload):
        return self._install(self.loader.decode(payload))

    def _install(self, plan):
        with self.g.lock:
            if self.busy: raise ValueError('Stop existing replay before loading')
            try: live,_=self.live()
            except ValueError: live=None
            if live is not None: compatible(plan['spec'],live)
            self.model_verified=live is not None
            self.plan=plan;self.state='loaded';self.reason='';self.detail='';self.owner=None;self.frame=None;self.commit=None
            self.diagnostic_id=None;self.native_status=None;self.operator_return=False;self.admission_failure=None
            self.entry_phase=None;self.returning=False;self.handoff=None
            return self.status()

    def _admit(self, initial=False):
        g=self.g
        g.refresh_feedback()
        if not g.physical or g.backend.id!='sonic' or g.phase!='active' or not g.alive(): raise ValueError('Active owned physical SONIC required')
        mode_pending=bool(g.mode_pending)
        ready=g.feedback_ready()
        telemetry=g.telemetry()
        safe=g.robot.safe(telemetry)
        if mode_pending or not ready or not safe:
            now=g.clock()
            health=getattr(g.robot,'health',None)
            transport=getattr(g,'transport',None)
            self.admission_failure=dict(at_monotonic_s=now,mode_pending=mode_pending,
                input_mode=g.input_mode,feedback_ready=ready,robot_safe=safe,
                telemetry=deepcopy(telemetry),
                topic_age_ms={k:(now-v[0])*1000 for k,v in g.latest.items() if k in g.robot.state_topics},
                feedback_timing=deepcopy(getattr(g,'feedback_timing',{})),
                continuity=deepcopy(getattr(health,'status',None)),
                continuity_changed_at=getattr(health,'changed_at',None),
                feedback_receiver=(g.feedback_inbox.snapshot() if g.feedback_inbox is not None else None),
                transport_stage=getattr(transport,'stage',None),
                transport_last_tick=getattr(transport,'last_tick',None))
            raise ValueError('Feedback or mode unavailable')
        p=g.protection_executor.snapshot() if g.protection_executor else {}
        if (any(p.get(k) is not True for k in ('configured','fresh','armed')) or
            any(p.get(k) is not False for k in ('fenced','estopped','takeover_requested')) or p.get('error')):
            raise ValueError('Protected writer unavailable')
        live,_=self.live(include_sample=False);compatible(self.plan['spec'],live)
        identity=p.get('recording_identity',{})
        if (p.get('wbc_pid')!=g.process.pid or identity.get('wbc_pid')!=g.process.pid
            or identity.get('bridge_pid')!=p.get('bridge_pid') or identity.get('generation')!=p.get('generation')
            or any(identity.get(k)!=live['artifacts'][k] for k in ('native_executable','bridge_executable'))):
            raise ValueError('Live native/writer identity mismatch')
        binding=(g.process.pid,p['bridge_pid'],p['generation'],p['stream'],live['artifacts']['native_executable'],live['artifacts']['bridge_executable'])
        if self.binding is not None and not initial and binding!=self.binding: raise ValueError('Replay authority generation changed')
        if not initial and g.input_mode!='replay':raise ValueError('Replay lost mode ownership')
        if initial:
            if not live['loaded_parameters'].get('is_using_encoder'):raise ValueError('Live Planner encoder required for replay return')
            if not g.policy_feedback or g.clock()-g.policy_feedback[0]>.25 or g.policy_feedback[1].get('mode')!='planner' or g.policy_feedback[1].get('playing') is not True:
                raise ValueError('Actual Planner feedback required')
            if not g.stationary(g.telemetry()) or (g.lease.owner and g.lease.owner!=self.owner and g.lease.deadline>g.clock()):
                raise ValueError('Stationary unowned Planner required')
        return binding

    def start(self, owner):
        if self.state!='loaded': raise ValueError('Load a new plan before start')
        self.owner=owner;self.binding=self._admit(initial=True);self.model_verified=True
        self.client=self.client_factory(self.path,self.g.process.pid)
        self.run=secrets.randbelow(2**52-1)+1;self.native_seq=0
        self.frame=None;self.commit=None;self.state='loading';self.reason='';self.run_started=self.clock()
        self.entry_phase='warming';self.returning=False;self.handoff=None
        self.g.lease.stop();self.g.last_packet=None;self.g.input_mode='replay';self.g.still_since=None
        return self.status()

    def return_to_planner(self):
        if self.state not in ('loading','starting','running'):return self.status()
        if not self.operator_return:
            _,sample=self.live()
            if not sample:raise ValueError('Live execution feedback required to return to Planner')
            self.operator_return=True
            if self.commit is None:self.commit=deepcopy(sample['output_commit'])
        return self.status()

    def stop(self, reason='operator_stop'):
        if not self.busy:return self.status()
        if self.state!='stopping':
            self.state='stopping';self.reason=reason
            # Preserve native timing evidence in the lifecycle journal, separately
            # from high-rate consumption logs which can rotate after shutdown.
            logging.getLogger('runtime_audit').warning(json.dumps(dict(
                event='reference_replay_stop',run=self.run,reason=reason,
                source_frame_position=self.frame,detail=self.detail)))
            if reason not in ('operator_stop','completed'):
                # Detach bounded evidence before shutdown; never wait for disk I/O.
                try:
                    from .incident import save_incident
                    receiver=getattr(self.g,'native_consumption',None)
                    evidence={}
                    if receiver:
                        with receiver.lock:
                            evidence=dict(stages=deepcopy(getattr(receiver,'diagnostics',{})),
                                receiver_error=receiver.error,
                                pending_replay_evidence=len(getattr(receiver,'replay_evidence',())),
                                last={k:deepcopy(v) for k,v in (receiver.join.last or {}).items()
                                      if k in ('index','timestamp_monotonic_ns','output_commit','replay','native_mode','token_source')})
                    self.diagnostic_id=save_incident(dict(run=self.run,reason=reason,detail=self.detail,
                        at_ns=time.monotonic_ns(),source_frame_position=self.frame,
                        output_commit=deepcopy(self.commit),evidence=evidence,native_status=deepcopy(self.native_status),
                        admission=deepcopy(self.admission_failure),
                        runtime_diagnostics=(self.g.runtime_diagnostics.snapshot()
                            if getattr(self.g,'runtime_diagnostics',None) is not None else None),
                        protection=self.g.protection_executor.snapshot() if self.g.protection_executor else {}))
                except Exception:
                    logging.getLogger('runtime_audit').exception('Replay incident capture failed')
            # Deliberate protected exit, not halt()'s cancellation/estop branch.
            # Existing writer detects the owned child's exit and owns all landing output.
            if self.g.alive(): self.g.halt('reference_replay:'+reason)
        return self.status()

    def status(self):
        return dict(schema='onboardmimic.execution.status',schema_version=2,
            diagnostic_id=self.diagnostic_id,plan_id=self.plan['id'] if self.plan else None,model_validation='matched_live' if self.model_verified else 'pending_live_instance',run_id=self.run,state=self.state,reason=self.reason,detail=self.detail,
            entry_phase=self.entry_phase,source_frame_position=self.frame,source_frame_index=None if self.frame is None else self.plan['source_indices'][self.frame],
            timing=self.plan.get('timing') if self.plan else None,
            source_reference_id=None if self.frame is None else self.plan['source_reference_ids'][self.frame],
            output_commit=self.commit,progress_boundary='protected_writer_dds_publish_returned_not_motor_execution',
            completion_ack=self.state=='completed' and self.handoff is not None,
            completion_boundary='live_planner_protected_output_confirmed',planner_handoff=self.handoff,
            stop_ack=self.state in ('stopped','failed'),
            stop_boundary='owned_wbc_exited_and_protected_writer_terminal_not_contact_verified',
            task_lifetime='runtime_owned',browser_lease_required=False)

    def _progress(self):
        try:
            live,last=self.live();receiver=self.g.native_consumption
            with receiver.lock:
                queue=getattr(receiver,'replay_evidence',None)
                samples=list(queue) if queue is not None else [last]
                if queue is not None:queue.clear()
            now_ns=int(self.clock()*1e9)
            contract=fingerprint(live)
            for sample in samples:
                replay=sample.get('replay') if sample else None
                if not replay or replay.get('run')!=self.run or replay.get('scheduled_frame') is not True:continue
                frame=replay['frame'];commit=sample['output_commit']
                if (type(frame) is not int or not 0<=frame<len(self.plan['source_indices']) or
                    not 0<=now_ns-sample['timestamp_monotonic_ns'] or
                    (queue is None and now_ns-sample['timestamp_monotonic_ns']>100000000) or
                    (queue is not None and sample['timestamp_monotonic_ns']<int(self.run_started*1e9)) or
                    (last and sample.get('session_id')!=last.get('session_id')) or
                    sample['contract_sha256']!=contract or
                    commit['generation']!=self.binding[2] or commit['stream']!=self.binding[3]):continue
                # The bounded queue contains already committed execution history.
                # Its drain time must not erase frames that were valid on arrival.
                # Current feedback/consumption freshness remains enforced in step().
                expected=0 if self.frame is None else self.frame+1
                if frame>expected:
                    self.detail=json.dumps(dict(expected_frame=expected,received_frame=frame,
                        evidence_age_ms=round((now_ns-sample['timestamp_monotonic_ns'])/1e6,3),
                        queued_samples=len(samples),inference_index=sample.get('index')),separators=(',',':'))
                    self.stop('consumption_gap');return
                if self.frame is None or frame>=self.frame:
                    self.frame=frame;self.commit=deepcopy(commit);self.state='running'
        except (ValueError,KeyError,TypeError):return

    def step(self):
        diagnostics=getattr(self.g,'runtime_diagnostics',None)
        if diagnostics is not None:diagnostics.mark('replay','control_lock')
        with self.g.lock:
            if not self.busy:return
            if self.state=='stopping':
                p=self.g.protection_executor.snapshot() if self.g.protection_executor else {}
                if (not self.g.alive() and p.get('fresh') and p.get('fenced') and
                    p.get('generation')==self.binding[2] and p.get('bridge_pid')==self.binding[1] and p.get('wbc_pid')==self.binding[0] and p.get('stream')==self.binding[3] and (p.get('phase') in (2,3) or (self.reason=='estop' and p.get('estopped')))):
                    if self.reason=='completed':self.reason='unexpected_completed_stop'
                    if p['phase']==3 and self.reason!='estop':self.reason='protection_aborted:'+str(p.get('reason','unknown'))
                    self.state='stopped' if self.reason in ('operator_stop','estop') else 'failed'

                return
            if diagnostics is not None:diagnostics.mark('replay','progress')
            self._progress()
            if self.state=='stopping':return
            if diagnostics is not None:diagnostics.mark('replay','admission')
            try:self._admit()
            except (ValueError,KeyError) as exc:
                self.detail=str(exc);return self.stop('runtime_admission_lost')
            stage=self.state;client=self.client
        # Socket and JSON work stay off the motor gateway thread/lock.
        if diagnostics is not None:diagnostics.mark('replay','native_request')
        if stage=='loading':
            result=client.request('load',**self.plan['native'])
            if result.get('entry_protocol')!=3:raise ValueError('Native first-frame transition protocol unavailable')
            with self.g.lock:
                if self.state!='loading':return
                self._admit()
                client.request('start',run=self.run);self.state='starting';self.run_started=self.clock()
            return
        if (self.operator_return or self.frame==len(self.plan['source_indices'])-1) and not self.returning:
            client.request('cancel_to_planner' if self.operator_return else 'finish',run=self.run)
            with self.g.lock:
                if self.state=='stopping':return
                self.returning=True;self.entry_phase='returning'
        self.native_seq+=1
        result=client.request('renew',run=self.run,sequence=self.native_seq)
        handoff=None
        self.native_status=deepcopy(result)
        if diagnostics is not None:diagnostics.mark('replay','result_lock')
        with self.g.lock:
            if self.state=='stopping':return
            if diagnostics is not None:diagnostics.mark('replay','result_validation')
            self.entry_phase=result.get('phase',self.entry_phase)
            _,sample=self.live()
            replay=sample.get('replay') if sample else None
            self._progress()
            if self.state=='stopping':return
            if result['state']=='stopping':
                if result.get('schedule') is not None:
                    self.detail=json.dumps(dict(phase=self.entry_phase,schedule=result['schedule']),sort_keys=True)
                return self.stop(result['reason'])
            if self.returning and result.get('return_ready_ns'):
                # Native readiness alone is insufficient: require a fresh Planner
                # inference committed by the same protected writer after readiness.
                commit=sample.get('output_commit',{}) if sample else {}
                ready=result['return_ready_ns']
                if (sample and sample.get('native_mode')==0 and sample.get('token_source')=='encoder'
                    and sample.get('contract_sha256')==fingerprint(self.live()[0])
                    and ready<=sample['timestamp_monotonic_ns']<=self.clock()*1e9
                    and self.clock()*1e9-sample['timestamp_monotonic_ns']<=100000000
                    and commit.get('generation')==self.binding[2] and commit.get('stream')==self.binding[3]
                    and commit.get('sequence',0)>self.commit['sequence']):
                    handoff=deepcopy(commit)
            fresh=sample and 0<=self.clock()*1e9-sample['timestamp_monotonic_ns']<=100000000
            valid_mode=(self.returning and sample and sample.get('native_mode')==0) or (replay and replay.get('run')==self.run)
            if self.clock()-self.run_started>3.3 and (not fresh or not valid_mode):
                age_ms=None if not sample else round((self.clock()*1e9-sample['timestamp_monotonic_ns'])/1e6,3)
                self.detail=json.dumps(dict(phase=self.entry_phase,consumption_fresh=bool(fresh),
                    evidence_age_ms=age_ms,native_mode=sample.get('native_mode') if sample else None,
                    token_source=sample.get('token_source') if sample else None,valid_mode=bool(valid_mode)),separators=(',',':'))
                return self.stop('consumption_timeout')
        if handoff is not None:
            # Do not block motor gateway ticks on the native socket round trip.
            reply=client.request('release',run=self.run)
            with self.g.lock:
                if self.state=='stopping':return
                self._admit()
                if reply.get('state')!='completed':raise ValueError('Planner release not confirmed')
                self.handoff=handoff;self.state='completed';self.reason='operator_stop' if self.operator_return else 'completed';self.entry_phase='planner'
                self.g.lease.stop();self.g.last_packet=None;self.g.input_mode='planner';self.g.still_since=None

    def _work(self):
        diagnostics=getattr(self.g,'runtime_diagnostics',None)
        while not self.closed.wait(.01):
            try:
                if diagnostics is not None:diagnostics.mark('replay','step')
                self.step()
            except Exception as exc:
                with self.g.lock:
                    self.detail=str(exc)
                    if self.busy:
                        try:self.stop('execution_worker_failure')
                        except Exception:self.state='stopping';self.reason='stop_unconfirmed'
            finally:
                if diagnostics is not None:diagnostics.mark('replay','wait')

    def close(self):
        with self.g.lock:
            if self.busy:self.stop('runtime_shutdown')
        self.closed.set()
        if self.thread.ident is not None:self.thread.join(timeout=1)
