"""Measured-feedback lifecycle shared by physical deployment and isolated tests.
Only launch/stop adapters own processes; this module never imports a robot SDK.
"""
import json
import logging
import math
import secrets
import time
from .gateway import Gateway, PlannerLease


class Lifecycle(Gateway):
    def __init__(self, baseline, launch, read_log, guard, physical=False, clock=time.monotonic, *, robot, backend, require_continuity=True):
        super().__init__(baseline, clock)
        self.robot, self.backend = robot, backend
        self.lease = PlannerLease(min(.5, backend.max_speed), max_speed=backend.max_speed)
        self.require_continuity = require_continuity
        self.launch, self.read_log, self.guard = launch, read_log, guard
        self.physical = physical
        self.latest = {}
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

    def ingest(self, topic, value):
        with self.lock:
            self.robot.ingest(topic, value, self.clock())
            normalized = self.backend.decode_status(topic, value)
            if normalized is not None:
                self.policy_feedback = (self.clock(), normalized)
            if topic in self.robot.state_topics:
                self.latest[topic] = (self.clock(), value)

    def telemetry(self):
        return self.robot.telemetry(self.latest, self.baseline, self.clock())

    def feedback_ready(self):
        return not self.require_continuity or self.robot.feedback_ready(self.latest,self.clock())

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def halt(self, error=''):
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
            now = self.clock()
            if self.phase in ('stopped', 'error'):
                return
            if not self.alive():
                self.halt('机载控制进程已退出')
                return
            t = self.telemetry()
            if not self.robot.safe(t):
                self.halt('真实状态/IMU失效、关节故障或超速，已终止控制进程')
                return
            progress = self.backend.progress(self.read_log())
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
                self.start_pending = False
            if self.phase in ('loading', 'preparing', 'starting_policy') and now > self.phase_deadline:
                self.halt('回正或策略接管超时')
                return
            if self.phase != 'active':
                return
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

    def status(self):
        with self.lock:
            self.tick()
            t = self.telemetry()
            value = dict(state=self.phase, mode='onboard_real' if self.physical else 'onboard_isolated',
                        robot=self.robot.label, robot_id=self.robot.id, backend=self.backend.id,
                        backend_label=self.backend.label, capabilities=sorted(self.backend.capabilities),
                        motion_enabled=self.physical, pids=[self.process.pid] if self.alive() else [],
                        pose_ready=self.phase == 'prepared' and self.robot.safe(t),
                        prepare_available=self.phase in ('stopped', 'error') and not self.alive() and self.robot.safe(t),
                        control_transferred=self.phase == 'active',
                        prepare_seconds=self.backend.prepare_seconds, reference_planner_handoff_seconds=self.backend.handoff_seconds,
                        workflow_error=self.failure,
                        stationary=self.stationary(t),
                        default_speed=self.lease.default_speed, max_speed=self.backend.max_speed,
                        control_owner=self.lease.owner,
                        control_exclusive=True,
                        input_mode=self.input_mode, mode_switch_pending=self.mode_pending,
                        mode_switch_error=self.mode_error,
                        pose_adapter_ready=self.pose_reader is not None,
                        pose_input_ready=bool(self.pose_reader and self.pose_reader.snapshot()['ready']),
                        pose_input_detail=self.pose_reader.snapshot().get('reason','') if self.pose_reader else 'Pose 适配器未加载',
                        input_mode_switch_available=self.phase == 'active' and not self.mode_pending,
                        planner_control_allowed=self.input_mode == 'planner' and not self.mode_pending,
                        telemetry={k: v for k, v in t.items() if k not in ('q', 'dq')},
                        epoch=self.epoch, ticket=self.ticket(), ticket_ttl_ms=250,
                        lease_remaining_ms=max(0, round((self.lease.deadline-self.clock())*1000)))

            # Compatibility aliases for older HTTP clients.
            value['pico_pose_ready'] = value['pose_input_ready']
            value['pico_pose_detail'] = value['pose_input_detail']
            value.update(self.robot.feedback_status(self.latest,self.clock()))
            if not self.feedback_ready():
                for key in ('prepare_available','planner_control_allowed','input_mode_switch_available'):
                    value[key] = False
            value['planner_control_allowed'] &= 'planner' in self.backend.capabilities
            value['input_mode_switch_available'] &= 'pose' in self.backend.capabilities
            return value

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
        with self.lock:
            if route not in ('/api/controller', '/api/control'):
                raise ValueError('未知接口')
            if route == '/api/controller' and x.get('action') == 'estop':
                self.halt()
                return self.status()
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
                if action in ('select_pose', 'select_planner'):
                    self.select_mode('pose' if action == 'select_pose' else 'planner', x)
                elif action == 'prepare_pose':
                    if self.phase not in ('stopped', 'error') or self.alive():
                        raise ValueError('请先急停再重新准备')
                    if not self.robot.safe(self.telemetry()):
                        raise ValueError('桥接反馈未就绪；未启动控制器')
                    # This guard must inspect live conflicts, not a cached preflight report.
                    self.guard()
                    # A slow guard must not turn stale feedback/tickets into permission.
                    self.validate_fresh(x)
                    if not self.robot.safe(self.telemetry()):
                        raise ValueError('检查期间反馈过期')
                    self.lease = PlannerLease(self.lease.default_speed, max_speed=self.backend.max_speed)
                    self.failure = ''
                    self.still_since = None
                    self.phase = 'loading'
                    self.phase_deadline = self.clock()+self.backend.prepare_timeout
                    try:
                        self.process = self.launch()
                    except Exception as exc:
                        self.halt('启动失败：' + str(exc))
                        raise RuntimeError(self.failure) from exc
                elif action == 'start_policy':
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
            if at>self.mode_requested_at+.1 and now-at<.25 and expected and value.get('playing') is True:
                self.mode_pending=False;return
        if now-self.mode_requested_at>2:
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
