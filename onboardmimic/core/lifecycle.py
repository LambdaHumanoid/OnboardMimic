"""Measured-feedback lifecycle shared by physical deployment and isolated tests.
Only launch/stop adapters own processes; this module never imports a robot SDK.
"""
import json
import logging
import math
import secrets
import signal
import time
import threading
from copy import copy, deepcopy
from .gateway import Gateway, PlannerLease
from .protection import ProtectionMonitor


class Lifecycle(Gateway):
    def __init__(self, baseline, launch, read_log, guard, physical=False, clock=time.monotonic, *, robot, backend, require_continuity=True):
        super().__init__(baseline, clock)
        self.robot, self.backend = robot, backend
        self.lease = PlannerLease(min(.5, backend.max_speed), max_speed=backend.max_speed)
        self.require_continuity = require_continuity
        self.launch, self.read_log, self.guard = launch, read_log, guard
        self.physical = physical
        self.latest = {}
        self.transport_driven = False
        self.feedback_timing = {}
        self.feedback_inbox = None
        self._query_lock = threading.Lock()
        self._query_cache = None
        self.process = None
        self.failure = ''
        self.phase_deadline = 0.
        self.still_since = None
        self.start_pending = False
        self.pose_reader = None
        self.input_mode = 'planner'
        self.mode_pending = False
        self.mode_requested_at = 0.
        self.mode_error = ''
        self.policy_feedback = None
        self.allow_wbc_exit_tests = False
        self.last_exit_test = None
        self.protection_executor = None
        self.protection = ProtectionMonitor(clock, event_sink=lambda event:
            logging.getLogger('runtime_audit').warning(json.dumps(
                dict(event='wbc_protection', **event), ensure_ascii=False)))

    def ingest(self, topic, value, *, received_at=None):
        entered_at = self.clock()
        received_at = entered_at if received_at is None else received_at
        with self.lock:
            acquired_at = self.clock()
            self.robot.ingest(topic, value, acquired_at)
            normalized = self.backend.decode_status(topic, value)
            if normalized is not None:
                self.policy_feedback = (self.clock(), normalized)
            if topic in self.robot.state_topics:
                # Lock contention must not make an old received sample look new.
                previous = self.latest.get(topic)
                self.latest[topic] = (received_at, value)
                self.feedback_timing[topic] = dict(
                    received_monotonic_s=received_at,
                    ingest_lock_wait_ms=max(0., (acquired_at-entered_at)*1000),
                    inbox_wait_ms=max(0., (entered_at-received_at)*1000),
                    update_gap_ms=None if previous is None else (received_at-previous[0])*1000,
                    source_age_ms=None)
            if self.protection.session:
                try:
                    evidence = self.backend.protection_output(topic, value,
                        self.protection.session, self.clock())
                    if evidence is not None:self.protection.output(evidence)
                except Exception:
                    # Optional shadow adapter cannot interrupt the motor runtime.
                    self.protection_adapter_error = 'Invalid WBC output evidence'

    def refresh_feedback(self):
        # Caller serializes application with other control decisions. The inbox
        # lock is released before ingest; producers never acquire the gateway lock.
        with self.lock:
            if self.feedback_inbox is not None:
                for topic, received_at, value in self.feedback_inbox.drain():
                    self.ingest(topic, value, received_at=received_at)
                    self.feedback_inbox.mark_applied(topic, received_at, self.clock())

    def telemetry(self):
        return self.robot.telemetry(self.latest, self.baseline, self.clock())

    def feedback_ready(self):
        return not self.require_continuity or self.robot.feedback_ready(self.latest,self.clock())

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def halt(self, error='', *, estop=False):
        replay=getattr(self,'reference_replay',None)
        if replay and replay.busy:
            if not error and not estop:error='reference_replay:runtime_shutdown'
            if estop or replay.state!='stopping':
                replay.state='stopping';replay.reason='estop' if estop else error
        # Intentional shutdown must be fenced at the writer before killing WBC.
        # A missing acknowledgement is an unconfirmed stop, never permission
        # to kill WBC and accidentally trigger an armed roll.
        if self.protection_executor and (estop or not error):
            self.protection_executor.cancel()
        try:
            if estop:self.protection.stop('estop', estop=True)
            elif error:
                t=self.telemetry()
                self.protection.fault(error, bool(self.robot.safe(t) and self.feedback_ready()))
            else:self.protection.stop()
        except Exception:
            self.protection_adapter_error = 'Protection diagnostics unavailable'
        if self.pose_reader: self.pose_reader.release_reference()
        # Caller holds lock: a queued start cannot survive this generation change.
        self.epoch = secrets.token_hex(16)
        self.sequence.clear()
        self.lease.stop()
        self.input_mode = 'planner'
        self.mode_pending = False
        self.last_packet = None
        self.start_pending = False
        self.still_since = None
        self.phase = 'error' if error else 'stopped'
        self.failure = error
        if self.alive():
            self.process.kill()
            try:
                self.process.wait(timeout=3)
            except Exception as exc:
                self.phase = 'error'
                self.failure = '控制进程退出未确认：' + str(exc)
                raise RuntimeError(self.failure) from exc

    def tick(self):
        with self.lock:
            self.refresh_feedback()
            now = self.clock()
            if self.phase in ('stopped', 'error'):
                return
            t = self.telemetry()
            try:
                self.protection.observe(self.alive(), bool(self.robot.safe(t) and self.feedback_ready()))
            except Exception:
                self.protection_adapter_error = 'Protection diagnostics unavailable'
            if not self.alive():
                self.halt('机载控制进程已退出')
                return
            if not self.robot.safe(t):
                self.halt('真实状态/IMU失效、关节故障或超速，已终止控制进程')
                return
            # Log progress is needed only during startup. Active feedback and
            # protection checks above still run on every control tick.
            progress = (self.backend.progress(self.read_log())
                        if self.phase in ('loading', 'preparing', 'prepared', 'starting_policy') else None)
            if self.phase in ('loading', 'preparing', 'prepared') and progress.armed:
                self.halt('检测到未请求的策略接管')
                return
            if self.phase in ('loading', 'preparing'):
                if progress.prepared:
                    self.phase = 'prepared'
                elif progress.preparing:
                    self.phase = 'preparing'
            elif self.phase == 'starting_policy' and progress.active:
                self.phase = 'active'
                self.protection.activate()
                self.start_pending = False
            if self.phase in ('loading', 'preparing', 'starting_policy') and now > self.phase_deadline:
                self.halt('回正或策略接管超时')
                return
            if self.phase != 'active':
                return
            if self.input_mode=='replay':
                self.last_packet=None;self.still_since=None;return
            self.tick_mode(now)
            if self.phase != 'active': return
            if self.input_mode == 'pose' or self.mode_pending:
                self.last_packet = None
                self.still_since = None
                return
            mode, movement, facing, speed = self.lease.sample(now)
            still = (t['upright'] and t['max_speed_rad_s'] < .6 and
                     not self.lease.move and not self.lease.turn)
            self.still_since = (now if self.still_since is None else self.still_since) if still else None
            self.last_packet = dict(mode=mode, movement_direction=movement,
                                    facing_direction=facing, speed=speed)

    def query_status(self):
        # Serialize redundant GET rendering, never while holding the control lock.
        if not self.transport_driven:
            return self.status()
        with self._query_lock:
            with self.lock:
                key=(self.epoch,self.phase,self.process,self.input_mode,self.mode_pending,self.failure)
                cached=self._query_cache
                reuse=bool(cached and cached[0]==key and 0<=self.clock()-cached[1]._status_at<.05
                    and cached[2].get('lab',{}).get('reason') != 'runtime_feedback_unavailable')
            if reuse:
                view,value=cached[1],deepcopy(cached[2])
            else:
                view=self._capture_status()
                value=view._render_status()
                self._query_cache=(key,view,deepcopy(value))
        return self._validate_status(view,value)

    def status(self):
        view=self._capture_status()
        return self._validate_status(view,view._render_status())

    def _capture_status(self):
        with self.lock:
            if not self.transport_driven:
                self.tick()  # SDK-free standalone harness compatibility.
            else:
                # A GET must validate received feedback, not wait for the next
                # control tick to apply it. Ingest preserves receive timestamps.
                self.refresh_feedback()
            view = copy(self)
            view._query_cache = None  # A snapshot must not retain prior snapshots.
            view.latest = dict(self.latest)  # ingest replaces, never mutates packets.
            view.lease = copy(self.lease)
            view.feedback_timing = {k: dict(v) for k, v in self.feedback_timing.items()}
            view._status_at = self.clock()
            view._status_ticket = self.ticket()
            view._status_protection = self.protection.snapshot()
            view._status_feedback_ready = self.feedback_ready()
            view._status_feedback_pipeline = self.feedback_inbox.snapshot() if self.feedback_inbox is not None else None
            view.robot = copy(self.robot)
            if hasattr(self.robot, 'health'):
                view.robot.health = copy(self.robot.health)
            replay = getattr(self, 'reference_replay', None)
            view._status_execution = replay.status() if replay else None
            view._status_replay_owner = replay.owner if replay and replay.busy else None
        # Only the snapshot is rendered; slow process/pose/native readers cannot
        # hold the real control lock. Commands retaining an outer lock still
        # retain their original serialization and validation semantics.
        view._status_feedback = view.robot.feedback_status(view.latest, view._status_at)
        return view

    def _validate_status(self, view, value):
        with self.lock:
            # Rendering can take longer than the feedback freshness budget.
            # Apply arrivals during rendering before the final safety check.
            self.refresh_feedback()
            value['status_snapshot_age_ms'] = max(0., (self.clock()-view._status_at)*1000)
            feedback_ready = self.feedback_ready()
            telemetry = self.telemetry()
            safe = self.robot.safe(telemetry)
            failures = []
            if self.phase != view.phase: failures.append('recording_control_state_changed')
            if self.process is not view.process: failures.append('recording_control_process_changed')
            if not feedback_ready: failures.append('runtime_feedback_unavailable')
            if not safe:
                if not telemetry.get('fresh'): reason = 'recording_joint_feedback_stale'
                elif not telemetry.get('valid'): reason = 'recording_joint_feedback_invalid'
                elif telemetry.get('faults'): reason = 'recording_joint_fault'
                elif telemetry.get('max_speed_rad_s', 99) > 12.5: reason = 'recording_joint_overspeed'
                else: reason = 'recording_robot_unsafe'
                failures.append(reason)
            if value['status_snapshot_age_ms'] > 250: failures.append('recording_status_snapshot_expired')
            lab = value['lab']
            # Record the final gate, not just the earlier native evidence check.
            # No I/O or process polling under the control lock.
            lab['recording_validation'] = dict(
                checked_monotonic_ns=int(self.clock()*1e9), failures=failures,
                snapshot_age_ms=value['status_snapshot_age_ms'], snapshot_limit_ms=250,
                captured_state=view.phase, current_state=self.phase,
                process_changed=self.process is not view.process,
                captured_pid=getattr(view.process, 'pid', None), current_pid=getattr(self.process, 'pid', None),
                captured_feedback_pipeline=view._status_feedback_pipeline,
                feedback_pipeline=self.feedback_inbox.snapshot() if self.feedback_inbox is not None else None,
                captured_feedback_ready=view._status_feedback_ready,
                captured_feedback_ages_ms={topic:max(0.,(view._status_at-view.latest[topic][0])*1000)
                    for topic in ('rt/lowstate','rt/secondary_imu','rt/continuity') if topic in view.latest},
                captured_feedback_continuity=view._status_feedback.get('feedback_continuity'),
                feedback_ready=feedback_ready,
                feedback_age_ms=telemetry.get('age_ms'),
                feedback_ages_ms={topic:max(0.,(self.clock()-self.latest[topic][0])*1000)
                    for topic in ('rt/lowstate','rt/secondary_imu','rt/continuity') if topic in self.latest},
                feedback_fresh=telemetry.get('fresh'),
                feedback_valid=telemetry.get('valid'), joint_faults=telemetry.get('faults'),
                max_joint_speed_rad_s=telemetry.get('max_speed_rad_s'), joint_speed_limit_rad_s=12.5,
                native_record_ready=lab.get('record_ready') is True, native_reason=lab.get('reason', ''))
            if failures:
                # Preserve a native fault; a snapshot refresh cannot clear it.
                lab.update(record_ready=False, native_consumption_fresh=False,
                           reason=lab.get('reason') or failures[0])
                for key in ('prepare_available', 'planner_control_allowed', 'input_mode_switch_available'):
                    value[key] = False
            # A mode transition invalidates mode-dependent buttons, not decoder evidence.
            if self.epoch != view.epoch or self.input_mode != view.input_mode or self.mode_pending != view.mode_pending:
                value['input_mode_switch_available'] = value['planner_control_allowed'] = False
        return value

    def _render_status(self):
        t = self.telemetry()
        alive = self.alive()
        pose = self.pose_reader.snapshot() if self.pose_reader else {}
        value = dict(state=self.phase, mode='onboard_real' if self.physical else 'onboard_isolated',
                    robot=self.robot.label, robot_id=self.robot.id, backend=self.backend.id,
                    backend_label=self.backend.label, capabilities=sorted(self.backend.capabilities),
                    motion_enabled=self.physical, pids=[self.process.pid] if alive else [],
                    pose_ready=self.phase == 'prepared' and self.robot.safe(t),
                    prepare_available=self.phase in ('stopped', 'error') and not alive and self.robot.safe(t),
                    control_transferred=self.phase == 'active',
                    prepare_seconds=self.backend.prepare_seconds, reference_planner_handoff_seconds=self.backend.handoff_seconds,
                    workflow_error=self.failure,
                    stationary=self.stationary(t),
                    default_speed=self.lease.default_speed, max_speed=self.backend.max_speed,
                    control_owner=self.lease.owner,
                    control_exclusive=True,
                    input_mode=self.input_mode, mode_switch_pending=self.mode_pending,
                    mode_switch_error=self.mode_error,
                    pose_entry_transition=self.input_mode=='pose' and self.mode_pending,
                    pose_adapter_ready=self.pose_reader is not None,
                    pose_skeleton=self.pose_reader.skeleton(published=self.phase == 'active' and self.input_mode == 'pose') if self.pose_reader else None,
                    pose_input_ready=bool(self.pose_reader and pose.get('ready', False)),
                    pose_input_detail=pose.get('reason','') if self.pose_reader else 'Pose 适配器未加载',
                    input_mode_switch_available=self.phase == 'active' and not self.mode_pending,
                    planner_control_allowed=self.input_mode == 'planner' and not self.mode_pending,
                    telemetry={k: v for k, v in t.items() if k not in ('q', 'dq')},
                    epoch=self.epoch, ticket=self._status_ticket, ticket_ttl_ms=250,
                    protection=self._status_protection,
                    wbc_exit_tests_enabled=self.allow_wbc_exit_tests,
                    last_exit_test=self.last_exit_test,
                    lease_remaining_ms=max(0, round((self.lease.deadline-self.clock())*1000)))

        # Compatibility aliases for older HTTP clients.
        value['pico_pose_ready'] = value['pose_input_ready']
        value['protection_executor'] = self.protection_executor.snapshot() if self.protection_executor else {'configured': False}
        if self.protection_executor:
            execution=value['protection_executor']
            value['protection'].update(mode='experimental_native',
                automatic_execution=bool(execution.get('fresh') and (execution.get('armed') or execution.get('phase')==1)),
                authority_transfer='native_writer',output_monitoring='native_command_sequence',
                runtime_crash_coverage=bool(execution.get('fresh') and execution.get('armed')))
        value['protection']['adapter_error'] = getattr(self, 'protection_adapter_error', '')
        value['pico_pose_detail'] = value['pose_input_detail']
        value.update(self._status_feedback)
        value['feedback_timing'] = dict(boundary='gateway_ingest_entry_not_sensor_capture', topics=self.feedback_timing)
        if not self._status_feedback_ready:
            for key in ('prepare_available','planner_control_allowed','input_mode_switch_available'):
                value[key] = False
        value['planner_control_allowed'] &= 'planner' in self.backend.capabilities
        value['input_mode_switch_available'] &= 'pose' in self.backend.capabilities
        native = getattr(self, 'native_consumption', None)
        value['lab'] = native.status(dict(
            active=self.phase == 'active' and alive,
            pid=self.process.pid if alive else None,
            input_mode=self.input_mode, mode_switch_pending=self.mode_pending,
            feedback_ready=self._status_feedback_ready, pose_input_ready=value['pose_input_ready'],
            physical=self.physical, protection=value['protection_executor'])) if native else dict(
                record_ready=False, contract_sha256=None, session_id=None,
                reason='native_telemetry_not_enabled')
        if self.phase != 'active' or not self._status_feedback_ready:
            value['lab']['native_consumption_fresh'] = False
        if self._status_execution is not None:
            value['execution'] = self._status_execution
            if self._status_replay_owner is not None:
                value['control_owner'] = self._status_replay_owner
                value['input_mode_switch_available'] = value['planner_control_allowed'] = False
        return value

    def execution_status(self, route):
        replay=getattr(self,'reference_replay',None)
        if not replay or not replay.plan or route!='/api/execution/v2/plans/'+replay.plan['id']:raise ValueError('Unknown replay plan')
        with self.lock:return replay.status()

    def execution_request(self, route, x):
        replay=getattr(self,'reference_replay',None)
        if not replay:raise ValueError('SONIC reference replay is not enabled')
        if route=='/api/execution/v2/plans':return replay.load(x['plan'])
        with self.lock:
            self.validate(x)
            if not replay.plan:raise ValueError('No loaded replay plan')
            base='/api/execution/v2/plans/'+replay.plan['id']+'/'
            if not route.startswith(base):raise ValueError('Unknown replay plan')
            action=route[len(base):]
            if action=='start':return replay.start(x['client'])
            if x.get('client')!=replay.owner or x.get('run_id')!=replay.run:raise ValueError('Replay owner/run mismatch')
            if action=='stop':return replay.return_to_planner()
            raise ValueError('Unknown replay action')

    def execution_load_bytes(self, payload):
        replay=getattr(self,'reference_replay',None)
        if not replay:raise ValueError('SONIC reference replay is not enabled')
        return replay.load_bytes(payload)

    def stationary(self, t):
        return bool(self.input_mode == 'planner' and not self.mode_pending and self.phase == 'active' and self.robot.safe(t) and t.get('upright') and
                    t['max_speed_rad_s'] < .6 and
                    not self.lease.move and not self.lease.turn and self.still_since is not None and
                    self.clock()-self.still_since >= .5)

    def request(self, route, x):
        # Record discrete requests only. Never log tokens, tickets or joint data.
        discrete=bool(x.get('action') or x.get('stop'))
        info={'route':route,'action':x.get('action'),
              'stop':bool(x.get('stop')),'client':x.get('client')}
        try:
            result=self._request(route,x)
        except (ValueError,RuntimeError) as exc:
            if discrete:logging.getLogger('control_audit').info(json.dumps(dict(info,result='rejected',reason=str(exc)),ensure_ascii=False))
            raise
        if discrete:logging.getLogger('control_audit').info(json.dumps(dict(info,result='accepted',state=result.get('state')),ensure_ascii=False))
        return result

    def _request(self, route, x):
        if route.startswith('/api/execution/v2/plans'):return self.execution_request(route,x)
        with self.lock:
            if route not in ('/api/controller', '/api/control'):
                raise ValueError('未知接口')
            if route == '/api/controller' and x.get('action') == 'estop':
                self.halt(estop=True)
                return self.status()
            replay=getattr(self,'reference_replay',None)
            if replay and replay.busy:
                self.validate(x)
                if route=='/api/control' and x.get('stop') is True:return replay.return_to_planner()
                raise ValueError('Replay owns control; stop replay before teleoperation or mode changes')
            if not self.feedback_ready() and not (route == '/api/control' and x.get('stop') is True):
                self.validate(x)
                raise ValueError('主备状态反馈未就绪或正在切换，暂不接受新的运动指令')
            self.tick()
            self.validate(x)
            for flag in ('stop',):
                if flag in x and type(x[flag]) is not bool:
                    raise ValueError('布尔控制参数无效')
            now = self.clock()
            if route == '/api/controller':
                action = x.get('action')
                if action in ('test_wbc_sigterm', 'test_wbc_sigkill'):
                    if not self.allow_wbc_exit_tests:
                        raise ValueError('真实退出测试未启用')
                    if self.phase != 'active' or not self.alive():
                        raise ValueError('只能测试当前拥有且正在运行的 WBC')
                    if self.protection.state != 'monitoring':
                        raise ValueError('保护监测未就绪或已经锁存')
                    if self.last_exit_test and self.last_exit_test['session']==self.protection.session:
                        raise ValueError('本次运行已发送退出测试，不自动重试')
                    if self.protection_executor:
                        if x.get('confirm_landing_execution') is not True:
                            raise ValueError('这是实际落地动作测试；请刷新控制台并确认动作，旧版请求已拒绝')
                        execution=self.protection_executor.snapshot()
                        if not execution.get('fresh') or not execution.get('armed'):
                            raise ValueError('保护执行器尚未确认布防，不能进行退出动作测试')
                    sig=signal.SIGTERM if action=='test_wbc_sigterm' else signal.SIGKILL
                    # Only the Popen child owned by this lifecycle. Never pkill,
                    # bridge signals, stale PID lookup or automatic restart.
                    self.last_exit_test=dict(action=action,pid=self.process.pid,
                        session=self.protection.session,requested_at=now,
                        effect='experimental_roll_takeover' if self.protection_executor else 'real_wbc_exit_no_landing_takeover')
                    self.process.send_signal(sig)
                elif action in ('select_pose', 'select_planner'):
                    self.select_mode('pose' if action == 'select_pose' else 'planner', x)
                elif action == 'prepare_pose':
                    if self.protection_executor and x.get('confirm_experimental_protection') is not True:
                        raise ValueError('准备姿态将开始持续监测并在首条 WBC 输出时自动布防；请刷新控制台并确认')
                    if self.phase not in ('stopped', 'error') or self.alive():
                        raise ValueError('请先急停再重新准备')
                    if not self.robot.safe(self.telemetry()):
                        raise ValueError('桥接反馈未就绪；未启动控制器')
                    # This guard must inspect live conflicts, not a cached preflight report.
                    guard_started = time.monotonic_ns()
                    try:
                        self.guard()
                    finally:
                        logging.getLogger('control_audit').info(json.dumps(dict(
                            event='prepare_admission_timing',
                            guard_ms=(time.monotonic_ns()-guard_started)/1e6,
                            ticket_age_ms=(self.clock()-float(x['ticket'].split(':')[0]))*1000)))
                    # A slow guard must not turn stale feedback/tickets into permission.
                    self.validate_fresh(x)
                    if not self.robot.safe(self.telemetry()):
                        raise ValueError('检查期间反馈过期')
                    if self.protection_executor:
                        reset_started = time.monotonic_ns()
                        try:
                            self.protection_executor.reset()
                        finally:
                            logging.getLogger('control_audit').info(json.dumps(dict(
                                event='prepare_protection_reset_timing',
                                reset_ms=(time.monotonic_ns()-reset_started)/1e6,
                                ticket_age_ms=(self.clock()-float(x['ticket'].split(':')[0]))*1000)))
                        self.validate_fresh(x)
                        if not self.robot.safe(self.telemetry()):
                            raise ValueError('保护会话初始化期间反馈过期')
                    self.lease = PlannerLease(self.lease.default_speed, max_speed=self.backend.max_speed)
                    self.failure = ''
                    self.still_since = None
                    self.phase = 'loading'
                    self.phase_deadline = self.clock()+self.backend.prepare_timeout
                    self.protection.begin(secrets.token_hex(16), self.backend.id)
                    try:
                        self.process = self.launch()
                        if self.protection_executor:
                            # Bind the child before its first command. The native
                            # writer arms atomically when accepting that command.
                            self.protection_executor.arm(self.process.pid)
                    except Exception as exc:
                        self.halt('启动失败：' + str(exc))
                        raise RuntimeError(self.failure) from exc
                elif action == 'start_policy':
                    if self.protection_executor and x.get('confirm_experimental_protection') is not True:
                        raise ValueError('启动将布防实验性落地保护；请刷新控制台并确认')
                    if self.phase != 'prepared' or not self.robot.safe(self.telemetry()) or not self.alive():
                        raise ValueError('请先完成回正且保持有效反馈')
                    self.phase = 'starting_policy'
                    self.phase_deadline = now+self.backend.handoff_timeout
                    self.start_pending = True
                else:
                    raise ValueError('未知操作')
            else:
                self.control_request(x, now)
            return self.status()

    def begin_mode(self,mode,reason=''):
        if self.pose_reader and mode!='pose': self.pose_reader.release_reference()
        self.lease.stop();self.last_packet=None
        self.epoch=secrets.token_hex(16);self.sequence.clear()
        self.input_mode=mode;self.mode_pending=True;self.mode_requested_at=self.clock()
        self.mode_error=reason;self.still_since=None

    def select_mode(self,mode,x):
        if mode not in self.backend.capabilities:
            raise ValueError('控制器不支持此模式：'+mode)
        if self.phase!='active' or not self.alive() or not self.robot.safe(self.telemetry()):
            raise ValueError('请先启动控制器并保持反馈有效')
        if self.mode_pending:raise ValueError('模式正在切换，请等待控制器确认')
        if mode==self.input_mode:return
        if mode=='pose':
            if not self.stationary(self.telemetry()):raise ValueError('请先停止行走并等待站稳')
            if not self.pose_reader or not self.pose_reader.snapshot()['ready']:raise ValueError('人体追踪姿态尚未就绪')
            if not self.policy_feedback or self.clock()-self.policy_feedback[0]>.5:
                raise ValueError('控制器 模式反馈未就绪')
            if not self.pose_reader.snapshot()['ready']:raise ValueError('检查期间人体追踪数据已过期')
            pose_check=self.pose_reader.snapshot()
            if not pose_check.get('pose_admission_ready',False):
                raise ValueError(pose_check.get('consistency',{}).get('reason','人体姿态一致性检查未通过'))
        if mode=='pose': self.pose_reader.freeze_reference()
        self.begin_mode(mode)

    def tick_mode(self,now):
        if self.input_mode=='pose':
            ready=bool(self.pose_reader and self.pose_reader.snapshot()['ready'])
            if not ready:
                self.begin_mode('planner','人体追踪断流，已请求返回 Planner；需手动重新进入 Pose')
        if self.input_mode=='pose' and not self.mode_pending:
            if (not self.policy_feedback or now-self.policy_feedback[0]>.5 or
                self.policy_feedback[1].get('mode')!='pose' or self.policy_feedback[1].get('playing') is not True):
                self.begin_mode('planner','控制器 Pose 状态失效，返回 Planner')
        if not self.mode_pending:return
        if self.policy_feedback:
            at,value=self.policy_feedback
            expected=value.get('mode')==self.input_mode
            completed=self.input_mode!='pose' or value.get('pose_entry_progress') == 1.0
            if at>self.mode_requested_at+.1 and now-at<.25 and expected and value.get('playing') is True and completed:
                if self.input_mode=='pose': self.pose_reader.release_reference()
                self.mode_pending=False;return
        if now-self.mode_requested_at>(5 if self.input_mode=='pose' else 2):
            if self.input_mode=='pose':self.begin_mode('planner','Pose 接管未确认，返回 Planner')
            else:self.halt('Planner 回退超时，控制器未确认模式')

    def validate_fresh(self, x):
        # validate() already consumed the sequence; only recheck its signed timestamp.
        if self.clock()-float(x['ticket'].split(':')[0]) > .25:
            raise ValueError('冲突检查期间请求已过期，请重试')

    def control_request(self, x, now):
        if x.get('stop') is True:
            self.lease.accept(x, now)
            if self.phase == 'active' and self.input_mode == 'pose':
                self.begin_mode('planner', '已请求停止 Pose，返回 Planner 待命')
            return
        if 'planner' not in self.backend.capabilities:
            raise ValueError('控制器不支持 Planner')
        if self.input_mode != 'planner' or self.mode_pending:
            raise ValueError('Pose 接管或切换中，Planner 指令已拒绝')
        if self.phase != 'active' or not self.alive() or not self.robot.safe(self.telemetry()):
            raise ValueError('请先准备姿态并启动 控制器，且保持有效反馈')
        self.lease.accept(x, now)
