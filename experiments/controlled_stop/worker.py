"""Explicitly armed experimental controller; isolated bridge, no SONIC changes."""
import json,os,secrets,socket,subprocess,threading,time
from pathlib import Path
import numpy as np,msgpack,zmq
from controller import Controller,BASE
from native_model import NativeModel
from wire import Encoder
from recording import Recorder,sample,provenance,json_safe
from control_api import ipc,NormalLifecycle
ROOT=Path(__file__).resolve().parent

class Experiment:
    def __init__(self,runtime_root,physical=False,shallow_plan=None,coordinated20=False,direct_six=False):
        if physical and (ROOT/'LOCKED').exists():raise RuntimeError('Experiment locked after hardware incident')
        self.runtime_root=runtime_root;self.physical=physical;self.bridge=None;self.lock=threading.Lock()
        self.lifecycle=NormalLifecycle(runtime_root) if physical else None;self.socket_path=ROOT/'handoff.sock';self.confirmed_handoff=False
        self.stop_event=threading.Event();self.abort=threading.Event();self.controller=None;self.phase='idle';self.error=''
        self.frame=None;self.captured=0.;self.heartbeat=0.;self.owner=None;self.lower_pending=False;self.stream=secrets.randbits(63)
        self.model=NativeModel(ROOT/'model.json')
        self.direct_mode=bool(direct_six);self.direct_plan=None;self.direct_template=None
        self.direct_state='waiting_for_sonic' if self.direct_mode else 'disabled';self.direct_error='';self.direct_directory=None
        if self.direct_mode:
            from direct_controller import DirectController,DirectTrajectory
            self.direct_controller_class=DirectController;self.direct_trajectory_class=DirectTrajectory
        self.shallow_enabled=shallow_plan is not None and not self.direct_mode
        self.shallow_plan=None
        self.plan_state='waiting_for_handoff' if self.shallow_enabled else 'disabled'
        self.plan_error=''
        self.coordinated_enabled=bool(coordinated20 and self.shallow_enabled)
        self.coordinated_plan=None;self.coordinated_state='waiting_for_shallow' if self.coordinated_enabled else 'disabled';self.coordinated_error=''
        if physical:
            profile=json.loads((ROOT/'effort-profile.json').read_text())
            if [x['joint'] for x in profile['joints']]!=self.model.spec['joint_names']:raise ValueError('Joint profile mismatch')
            self.model.spec['simulation_effort_limits_nm']=[min(x['urdf_effort_nm'],x['simulation_effort_nm']) for x in profile['joints']]
        self.trajectory=np.load(ROOT/'trajectory.npz');self.encoder=Encoder()
        self.recorder=None;self.record_until=float('inf');self.count=0;self.cycle_ms=[];self.snapshot={};self.generation=0
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def audit(self,event,**details):
        (ROOT/'logs').mkdir(exist_ok=True)
        with (ROOT/'logs/experiment.jsonl').open('a') as f:f.write(json.dumps(json_safe(dict(time=time.time(),event=event,**details)),ensure_ascii=False,allow_nan=False)+'\n')

    def status(self):
        with self.lock:
            fresh=self.frame is not None and 0<=time.monotonic()-self.captured<.05
            s=dict(self.snapshot)
            if self.recorder:s['recording']=dict(directory=str(self.recorder.directory),dropped=self.recorder.dropped,error=self.recorder.error,written=self.recorder.written)
            s.update(phase=self.phase,fresh=fresh,error=self.error,read_only=not self.physical,motor_commands_sent=self.count,
                     actuation_enabled=self.physical,bridge_alive=self.socket_path.exists(),
                     can_prepare=False,trial_stage=self.controller.trial_stage if self.controller else 'handoff_hold',
                     direct_six=self.direct_mode,direct_plan_state=self.direct_state,direct_plan_error=self.direct_error,
                     shallow_plan_state=self.plan_state,shallow_plan_error=self.plan_error,
                     coordinated_plan_state=self.coordinated_state,coordinated_plan_error=self.coordinated_error,
                     can_advance20=bool(self.coordinated_plan is not None and self.controller is not None and self.controller.trial_stage=='measured_shallow' and self.phase=='holding' and self.confirmed_handoff and fresh and self.recorder and not self.recorder.error and not self.recorder.dropped),
                     can_advance=bool(self.shallow_plan is not None and self.controller is not None and self.controller.trial_stage=='handoff_hold' and self.phase=='holding' and self.confirmed_handoff and fresh and self.recorder and not self.recorder.error and not self.recorder.dropped),
                     next_target_depth_m=.03 if self.shallow_enabled else None,
                     can_lower=self.physical and fresh and self.phase=='idle' and self.socket_path.exists() and (not self.direct_mode or self.direct_state=='ready'),
                     updated_wall=time.time())
            return s

    def command(self,action,client,confirmed=False):
        if action=='heartbeat':
            with self.lock:
                if client==self.owner:self.heartbeat=time.monotonic()
            return
        if action=='stop':
            self.abort.set()
            with self.lock:self.controller=None;self.phase='stopped';self.generation+=1;self.record_until=time.monotonic()+10
            try:ipc(self.socket_path,'cancel')
            except Exception:pass
            if self.lifecycle:self.lifecycle.action('estop')
            self.audit('experiment_stop_requested');return
        if not self.physical:raise ValueError('当前仅软件测试，不发送关节指令')
        if action in ('advance_shallow','advance20'):
            if not confirmed:raise ValueError('请确认可靠支撑、双脚落地且现场有人')
            with self.lock:
                if client!=self.owner:raise ValueError('请使用接管本轮实验的页面')
                plan=self.shallow_plan if action=='advance_shallow' else self.coordinated_plan
                if plan is None or self.controller is None or not self.confirmed_handoff or self.abort.is_set():raise ValueError('本轮计划尚未就绪')
                if self.frame is None or time.monotonic()-self.captured>.05:raise ValueError('反馈不新鲜')
                if not self.recorder or self.recorder.error or self.recorder.dropped:raise ValueError('录制状态异常')
                if time.monotonic()-self.heartbeat>=1:raise ValueError('页面心跳不新鲜')
                q,dq,quat,gyro,machine=self.state(self.frame)
                advance=self.controller.advance_shallow if action=='advance_shallow' else self.controller.advance_coordinated
                advance(plan,time.monotonic(),q,dq,quat)
                self.phase=self.controller.phase
                depth=.03 if action=='advance_shallow' else .20
                self.recorder.submit(dict(kind='manual_stage_advance',wall=time.time(),target_depth_m=depth,note='kinematic target, not measured displacement'))
            self.audit('manual_shallow_requested' if action=='advance_shallow' else 'manual_20cm_requested',target_depth_m=depth);return
        if action=='reset':
            if not confirmed:raise ValueError('请先确认可靠支撑')
            status=self.lifecycle.status()
            if status.get('pids') or status.get('state') not in ('stopped','error'):raise ValueError('请先停止实验及 SONIC')
            if self.socket_path.exists():
                b=ipc(self.socket_path,'snapshot')
                if b['state'] not in ('latched','idle'):raise ValueError('请等待桥接进入阻尼保持')
            with self.lock:
                if self.controller is not None:raise ValueError('实验仍在运行')
                self.generation+=1;self.abort.clear();self.phase='idle';self.error='';self.owner=None;self.confirmed_handoff=False;self.stream=secrets.randbits(63)
                self.shallow_plan=None;self.plan_state='waiting_for_handoff' if self.shallow_enabled else 'disabled';self.plan_error=''
                self.coordinated_plan=None;self.coordinated_state='waiting_for_shallow' if self.coordinated_enabled else 'disabled';self.coordinated_error=''
                self.direct_plan=None;self.direct_template=None;self.direct_state='waiting_for_sonic' if self.direct_mode else 'disabled';self.direct_error=''
            self.audit('reset');return

        if action in ('connect_services','prepare_pose','start_policy'):
            with self.lock:
                if self.phase!='idle':raise ValueError('下蹲实验进行中或已结束，不能重新启动 SONIC')
            if not confirmed:raise ValueError('请确认可靠支撑、双脚落地且现场有人')
            result=self.lifecycle.action(action)
            if action=='start_policy' and self.direct_mode:self.start_direct_planning()
            return result
        if action=='refresh_direct_plan':
            if not confirmed or not self.direct_mode:raise ValueError('请确认现场条件与实验模式')
            self.start_direct_planning();return
        if action!='lower':raise ValueError('Unknown action')
        if not confirmed:raise ValueError('请确认可靠支撑、双脚落地且现场有人')
        status=self.lifecycle.status()
        if status.get('state')!='active' or status.get('input_mode','planner')!='planner' or not status.get('stationary'):raise ValueError('请先正常启动 SONIC，保持 Planner 原地站立')
        with self.lock:
            if self.phase!='idle' or self.abort.is_set():raise ValueError('本轮已开始或已停止')
            if self.frame is None or time.monotonic()-self.captured>.05:raise ValueError('反馈不新鲜')
            q,dq,quat,gyro,machine=self.state(self.frame)
            # Validate all conditions BEFORE requesting handoff. No policy is
            # killed if admission, feedback or command continuity is rejected.
            snapshot=ipc(self.socket_path,'snapshot')
            now=time.monotonic()
            if self.direct_mode:
                if self.direct_plan is None or self.direct_template is None or self.direct_state!='ready':raise ValueError('6 秒计划尚未就绪，SONIC 保持运行')
                candidate=self.direct_controller_class.direct_handoff(self.model,self.direct_plan,q,dq,quat,snapshot['command'],now,prepared=self.direct_template)
            else:Controller.handoff(self.model,self.trajectory,q,dq,snapshot['command'],now,recovery_enabled=True,trial_stage='handoff_hold')
            if self.recorder is not None:self.recorder.closed.set()
            self.record_until=float('inf')
            self.recorder=Recorder(ROOT/'logs'/'recordings'/('%d-%s'%(time.time_ns(),self.stream)))
            self.recorder.submit(provenance(ROOT))
            self.recorder.submit(dict(kind='handoff_request',wall=time.time(),monotonic=time.monotonic(),frame=self.frame,snapshot=snapshot))
            if self.direct_mode:self.recorder.submit(dict(kind='direct_six_plan',directory=str(self.direct_directory),duration_s=6.,depth_m=.20))
            claimed=ipc(self.socket_path,'claim',expected_stream=snapshot['stream'],new_stream=self.stream)
            try:
                if self.direct_mode:self.controller=self.direct_controller_class.direct_handoff(self.model,self.direct_plan,q,dq,quat,claimed['command'],time.monotonic(),prepared=self.direct_template)
                else:self.controller=Controller.handoff(self.model,self.trajectory,q,dq,claimed['command'],time.monotonic(),recovery_enabled=True,trial_stage='handoff_hold')
                self.phase='lowering';self.owner=client;self.heartbeat=time.monotonic();generation=self.generation
            except Exception:
                ipc(self.socket_path,'cancel');raise
        self.audit('handoff_requested',old_stream=snapshot['stream'],new_stream=self.stream)
        threading.Thread(target=self.confirm_handoff,args=(generation,),daemon=True).start()

    def start_direct_planning(self):
        with self.lock:
            if self.phase!='idle' or self.abort.is_set():raise ValueError('请先结束当前实验')
            if self.direct_state=='generating':raise ValueError('正在生成本轮计划')
            self.direct_state='generating';self.direct_error='';self.direct_plan=None;self.direct_template=None
            generation=self.generation
        threading.Thread(target=self.prepare_direct,args=(generation,),daemon=True).start()

    def prepare_direct(self,generation):
        """Read-only planning while SONIC owns output. Never claims or stops it."""
        process=None
        try:
            deadline=time.monotonic()+60;ready_since=None
            while time.monotonic()<deadline:
                with self.lock:
                    if generation!=self.generation or self.abort.is_set() or self.phase!='idle':return
                status=self.lifecycle.status()
                ready=status.get('state')=='active' and status.get('input_mode')=='planner' and status.get('stationary')
                if not ready:ready_since=None
                elif ready_since is None:ready_since=time.monotonic()
                elif time.monotonic()-ready_since>=2:break
                if self.stop_event.wait(.25):return
            else:raise ValueError('等待 SONIC 原地站稳超时，可点击重新生成')
            with self.lock:
                if generation!=self.generation or self.abort.is_set() or self.phase!='idle':return
                if self.frame is None or time.monotonic()-self.captured>.05:raise ValueError('计划采样反馈不新鲜')
                q,dq,quat,gyro,machine=self.state(self.frame)
                if np.max(abs(dq))>.2:raise ValueError('计划采样时机器人尚未站稳')
                record=dict(raw_feedback=self.frame,wall=time.time())
            directory=ROOT/'logs'/'preplans'/str(time.time_ns());directory.mkdir(parents=True)
            source=directory/'source.json';target=directory/'direct-plan.npz';source.write_text(json.dumps(json_safe(record),allow_nan=False))
            import sys
            process=subprocess.Popen(['nice','-n','19',sys.executable,str(ROOT/'direct_plan.py'),str(source),str(target)],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1'))
            output,error=process.communicate(timeout=120)
            if process.returncode:raise ValueError('6 秒计划未通过检查: '+error[-500:])
            with np.load(target,allow_pickle=False) as data:plan={k:data[k].copy() for k in data.files}
            prepared=self.direct_trajectory_class(plan,np.asarray(self.model.spec['limits']))
            with self.lock:
                if generation!=self.generation or self.abort.is_set() or self.phase!='idle':return
                self.direct_plan=plan;self.direct_template=prepared;self.direct_directory=directory;self.direct_state='ready'
            self.audit('direct_plan_ready',directory=str(directory),duration_s=6.,depth_m=.20)
        except Exception as e:
            if process is not None and process.poll() is None:process.kill();process.communicate()
            with self.lock:
                if generation==self.generation and not self.abort.is_set():self.direct_state='error';self.direct_error=str(e);self.direct_plan=None;self.direct_template=None

    def confirm_handoff(self,generation):
        deadline=time.monotonic()+.3
        try:
            while time.monotonic()<deadline:
                snapshot=ipc(self.socket_path,'snapshot')
                if snapshot['stream']==self.stream:
                    with self.lock:
                        if generation!=self.generation or self.abort.is_set():return
                        self.confirmed_handoff=True
                    # Bridge already owns the new stream; the worker blends every field.
                    # Stopping the former policy now does not interrupt output.
                    self.lifecycle.action('estop');self.audit('handoff_confirmed')
                    if self.shallow_enabled:
                        threading.Thread(target=self.prepare_shallow,args=(generation,),daemon=True).start()
                    return
                time.sleep(.004)
            raise ValueError('下蹲控制权衔接未确认；未主动停止 SONIC')
        except Exception as e:
            with self.lock:self.controller=None;self.phase='fault';self.error=str(e);self.abort.set()
            try:ipc(self.socket_path,'cancel')
            except Exception:pass
            self.audit('handoff_failed',error=str(e))

    def prepare_shallow(self,generation,coordinated=False):
        """Plan in a separate low-priority process; never block the control lock.

        Publishing a plan only enables the manual button. It cannot start motion.
        Every reset/stop invalidates any pending result from the previous trial.
        """
        if self.stop_event.wait(2.):return
        process=None
        try:
            with self.lock:
                c=self.controller
                if generation!=self.generation or self.abort.is_set() or c is None:return
                expected='measured_shallow' if coordinated else 'handoff_hold'
                if c.phase!='holding' or c.trial_stage!=expected:return
                if self.frame is None or time.monotonic()-self.captured>.05:raise ValueError('计划采样时反馈不新鲜')
                q,dq,quat,gyro,machine=self.state(self.frame)
                if np.max(abs(dq))>.2:raise ValueError('计划采样时尚未站稳')
                record=dict(raw_feedback=self.frame,wall=time.time())
                directory=self.recorder.directory
                if coordinated:self.coordinated_state='generating';self.coordinated_error=''
                else:self.plan_state='generating';self.plan_error=''
            label='coordinated' if coordinated else 'shallow'
            source=directory/(label+'-source.json');target=directory/(label+'-plan.npz')
            source.write_text(json.dumps(json_safe(record),allow_nan=False))
            import sys
            env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
            script='coordinated_plan.py' if coordinated else 'standing_plan.py'
            command=['nice','-n','19',sys.executable,str(ROOT/script),str(source),str(target)]
            if coordinated:command.append('--from-shallow')
            process=subprocess.Popen(command,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            output,error=process.communicate(timeout=45 if coordinated else 20)
            if process.returncode:raise ValueError('本轮计划生成失败: '+error[-500:])
            with np.load(target,allow_pickle=False) as data:plan={k:data[k].copy() for k in data.files}
            with self.lock:
                if generation!=self.generation or self.abort.is_set() or self.controller is not c:return
                if c.phase!='holding' or c.trial_stage!=expected:return
                if coordinated:self.coordinated_plan=plan;self.coordinated_state='ready'
                else:self.shallow_plan=plan;self.plan_state='ready'
                self.recorder.submit(dict(kind=label+'_plan_ready',wall=time.time(),source=str(source),plan=str(target),target_depth_m=.20 if coordinated else .03))
            self.audit(label+'_plan_ready',source=str(source),plan=str(target))
        except Exception as e:
            if process is not None and process.poll() is None:
                process.kill();process.communicate()
            with self.lock:
                if generation==self.generation and not self.abort.is_set():
                    if coordinated:self.coordinated_state='error';self.coordinated_error=str(e);self.coordinated_plan=None
                    else:self.plan_state='error';self.plan_error=str(e);self.shallow_plan=None

    @staticmethod
    def state(frame):
        motors=frame['motor_state'][:29]
        if len(motors)!=29 or any(x.get('motorstate',0) for x in motors):raise ValueError('关节故障或反馈不完整')
        q=np.array([x['q'] for x in motors]);dq=np.array([x['dq'] for x in motors]);imu=frame['imu_state']
        quat=np.asarray(imu['quaternion']);gyro=np.asarray(imu['gyroscope'])
        if len(quat)!=4 or len(gyro)!=3 or not .9<np.linalg.norm(quat)<1.1:raise ValueError('IMU 无效')
        return q,dq,quat,gyro,frame['mode_machine']

    def run(self):
        ctx=zmq.Context();subs=[];poll=zmq.Poller();udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        for port in (16665,16666):
            s=ctx.socket(zmq.SUB);s.setsockopt(zmq.SUBSCRIBE,b'');s.setsockopt(zmq.RCVHWM,4);s.connect('tcp://127.0.0.1:%d'%port);subs.append(s);poll.register(s,zmq.POLLIN)
        last_tick=None;deadline=time.monotonic();last_status=0
        try:
            while not self.stop_event.is_set():
                begin=time.monotonic()
                for sub,_ in poll.poll(0):
                    for _ in range(16):
                        try:raw=sub.recv(zmq.NOBLOCK)
                        except zmq.Again:break
                        try:
                            e=msgpack.unpackb(raw,raw=False)
                            if e['topic']!='rt/lowstate' or not 0<=time.monotonic()-e['captured']<.05:continue
                            tick=e['tick']
                            if last_tick is not None and not 0<(tick-last_tick)%2**32<2**31:continue
                            frame=msgpack.unpackb(e['payload'],raw=False);self.state(frame)
                            with self.lock:self.frame=frame;self.captured=e['captured']
                            last_tick=tick
                        except (ValueError,KeyError,TypeError):continue
                with self.lock:
                    now=time.monotonic();fresh=self.frame is not None and 0<=now-self.captured<.05
                    c=self.controller
                    try:
                        if c is not None and not self.abort.is_set():
                            if not fresh:raise ValueError('反馈超过 50 ms 未更新')
                            if not self.socket_path.exists():raise ValueError('实验桥接不可用')
                            q,dq,quat,gyro,machine=self.state(self.frame)
                            if self.lower_pending:
                                self.lower_pending=False;c.lower(now,q,dq)
                            if not self.confirmed_handoff:
                                c.at=c.last=now;command=c.held.copy()
                            else:
                                if self.recorder and (self.recorder.error or self.recorder.dropped):
                                    c.pause_progress('recording_unhealthy')
                                command=c.tick(now,q,dq,quat,gyro,heartbeat=now-self.heartbeat<1.)
                            packet=self.encoder.encode(command,machine,self.stream)
                            # Stop and send serialized under the same lock; after
                            # stop returns, no new command can leave this worker.
                            if self.physical and not self.abort.is_set():udp.sendto(packet,('127.0.0.1',16663));self.count+=1
                            self.phase=c.phase
                            if self.coordinated_enabled and c.trial_stage=='measured_shallow' and c.phase=='holding' and self.coordinated_state=='waiting_for_shallow':
                                self.coordinated_state='generating'
                                threading.Thread(target=self.prepare_shallow,args=(self.generation,True),daemon=True).start()
                            if self.recorder:self.recorder.submit(sample(self.captured,self.frame,command,self.phase,c.diagnostics,emitted=self.physical and not self.abort.is_set(),sequence=self.encoder.sequence))
                        if self.recorder is not None and now>self.record_until:self.recorder.closed.set()
                        if c is None and self.recorder is not None and fresh:
                            self.recorder.submit(sample(self.captured,self.frame,None,self.phase))
                        if now-last_status>.2:
                            s={}
                            if fresh:
                                q,dq,quat,gyro,machine=self.state(self.frame)
                                s=dict(max_joint_speed=float(max(abs(dq))),faults=[],feedback_age_ms=(now-self.captured)*1000,q=q.tolist())
                            if self.cycle_ms:s['cycle_ms']=dict(p99=float(np.percentile(self.cycle_ms,99)),max=max(self.cycle_ms))
                            self.snapshot=s;last_status=now
                    except Exception as e:
                        context=getattr(c,'fault_context',None) if c is not None else None
                        if self.recorder:
                            self.recorder.submit(dict(kind='fault',wall=time.time(),monotonic=time.monotonic(),error=str(e),context=context,frame=self.frame,previous_command=c.previous.copy() if c is not None and c.previous is not None else None))
                        threading.Thread(target=self.audit,args=('controller_fault',),kwargs=dict(error=str(e),context=context,packets=self.count,frame=self.frame,captured=self.captured,last_command=c.previous.tolist() if c is not None and c.previous is not None else None),daemon=True).start()
                        self.controller=None;self.phase='fault';self.error=str(e);self.abort.set();self.record_until=min(self.record_until,time.monotonic()+10)
                self.cycle_ms.append((time.monotonic()-begin)*1000);self.cycle_ms=self.cycle_ms[-5000:]
                deadline+=.004
                if deadline<time.monotonic():deadline=time.monotonic()
                self.stop_event.wait(max(0,deadline-time.monotonic()))
        finally:
            if self.recorder:self.recorder.close()
            udp.close()
            for s in subs:s.close(0)
            ctx.term()
