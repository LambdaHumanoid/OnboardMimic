"""Experimental stationary controller; no transport, services or hardware imports."""
import numpy as np
from model import rotation

BASE=np.array([-.312,0,0,.669,-.363,0]*2+[0,0,0]+[.2,.2,0,.6,0,0,0]+[.2,-.2,0,.6,0,0,0])
ARM=np.array([.025101925,.025101925,.010177520,.025101925,2*.003609725,2*.003609725]*2+[.010177520,2*.003609725,2*.003609725]+[.003609725]*5+[.00425]*2+[.003609725]*5+[.00425]*2)
KP=ARM*(20*np.pi)**2;KD=4*ARM*20*np.pi

def smooth(t):
    x=np.clip(t,0,1);return x*x*(3-2*x)

class Controller:
    def __init__(self,model,trajectory,q,now):
        self.model=model;self.poses=trajectory['q'];self.times=trajectory['time']
        self.limits=np.asarray(model.spec['limits']);self.start=np.asarray(q).copy()
        if np.max(abs(self.start-BASE))>1.2:raise ValueError('当前姿态偏离准备姿态过大')
        self.handoff_seconds=.5;self.phase='preparing';self.at=now;self.last=now;self.motion_time=0.;self.previous=None
        self.stage_hold_since=None
        self.heading_rotation=None;self.heading_frame=True;self.anchor=None;self.com0=None;self.last_com=None;self.velocity=np.zeros(3)
        self.compensation_event=None;self.support=None;self.support_diagnostics=None;self.trial_stage='full';self.motion_limit=float(self.times[-1]);self.recovery_enabled=False;self.diagnostics=None;self.fault_context=None;self.entry_error=np.zeros(29);self.pause=False;self.hold_target=None;self.ready_since=None

    @classmethod
    def handoff(cls,model,trajectory,q,dq,command,now,*,recovery_enabled=False,trial_stage='full',constrained_support=False):
        c=cls(model,trajectory,q,now)
        if trial_stage not in ('handoff_hold','shallow','full'):raise ValueError('Invalid trial stage')
        c.trial_stage=trial_stage;c.motion_limit={'handoff_hold':0.,'shallow':1.,'full':float(c.times[-1])}[trial_stage]
        c.recovery_enabled=recovery_enabled
        if recovery_enabled:
            c.effort_limits=np.asarray(model.spec['simulation_effort_limits_nm'])
            if c.effort_limits.shape!=(29,) or not np.isfinite(c.effort_limits).all() or np.any(c.effort_limits<=0):raise ValueError('Invalid simulation effort limits')
        if constrained_support:
            if not recovery_enabled:raise ValueError('Constrained support is simulation-only recovery')
            from support import SupportAllocator
            c.support=SupportAllocator(c.effort_limits)
        previous=np.asarray(command,dtype=float)
        if previous.shape!=(29,5) or not np.isfinite(previous).all():raise ValueError('SONIC 初始指令无效')
        if max(abs(np.asarray(q)-BASE))>.8 or max(abs(np.asarray(dq)))>.2:raise ValueError('请等待 SONIC 原地站稳')
        c.entry_error=np.maximum(0,abs(np.asarray(q)-previous[:,0])-.35)
        c.previous=previous.copy();c.held=previous.copy();c.phase='lowering';c.at=now;c.motion_time=0.
        return c

    def pose_at(self,t):
        if self.trial_stage=='handoff_hold':return self.start.copy()
        if self.trial_stage=='measured_coordinated':
            target=np.array([np.interp(t,self.times,self.poses[:,i]) for i in range(29)])
            target+=(self.stage_start-self.poses[0])*(1-smooth(t/2.))
            target[15:]=self.stage_start[15:]
            return target
        if self.trial_stage=='measured_shallow':
            target=np.array([np.interp(t,self.times,self.poses[:,i]) for i in range(29)])
            target+=(self.start-self.poses[0])*(1-smooth(t/2.))
            target[12:]=self.start[12:]
            return target
        return np.array([np.interp(t,self.times,self.poses[:,i]) for i in range(29)])

    def velocity_at(self,t):
        return np.zeros(29)

    def advance_shallow(self,plan,now,q,dq,quat):
        """Explicit one-shot advance from confirmed hold; never auto-advance."""
        if self.trial_stage!='handoff_hold' or self.phase!='holding' or now-self.at<2:
            raise ValueError('请先完成接管并稳定保持至少两秒')
        poses=np.asarray(plan['q']);times=np.asarray(plan['time']);reference=np.asarray(plan['quaternion'])
        if poses.ndim!=2 or poses.shape[1]!=29 or len(poses)<61 or times.shape!=(len(poses),):
            raise ValueError('Invalid shallow plan shape')
        if not np.isfinite(np.r_[poses.ravel(),times,reference]).all() or reference.shape!=(4,) or not .99<np.linalg.norm(reference)<1.01:
            raise ValueError('Invalid shallow plan values')
        if times[0]!=0 or times[-1]<6 or np.any(np.diff(times)<=0) or not 0<float(plan['depth_m'])<=.03:
            raise ValueError('Invalid shallow plan duration/depth')
        if np.any(poses<self.limits[:,0]+.01) or np.any(poses>self.limits[:,1]-.01) or np.max(abs(np.diff(poses,axis=0)/np.diff(times)[:,None]))>.25:
            raise ValueError('Shallow plan exceeds joint/velocity bounds')
        # Only legs move. Upper-body targets remain this trial's hold targets;
        # harmless arm/wrist compliance must not reject a leg-only plan.
        if np.max(abs(np.asarray(dq)))>.2 or np.max(abs(np.asarray(q)[:12]-self.start[:12]))>.03 or np.max(abs(poses[0,:12]-self.start[:12]))>.03:
            raise ValueError('当前站姿与小幅下沉计划不一致，请重新采集和生成')
        a=np.asarray(quat)/np.linalg.norm(quat);b=reference/np.linalg.norm(reference)
        if abs(float(a@b))<np.cos(np.radians(2)/2):raise ValueError('当前骨盆方向与计划不一致')
        self.poses=poses.copy();self.times=times.copy();self.motion_time=0.;self.motion_limit=float(times[-1])
        self.trial_stage='measured_shallow';self.phase='lowering'
        self.stage_hold_since=None

    def advance_coordinated(self,plan,now,q,dq,quat):
        if self.trial_stage!='measured_shallow' or self.phase!='holding' or self.stage_hold_since is None or now-self.stage_hold_since<2:
            raise ValueError('请先完成小幅下沉并稳定保持至少两秒')
        poses=np.asarray(plan['q']);times=np.asarray(plan['time']);quats=np.asarray(plan['quaternion'])
        if poses.ndim!=2 or poses.shape[1]!=29 or len(poses)<121 or times.shape!=(len(poses),) or quats.shape!=(len(poses),4):raise ValueError('Invalid coordinated plan shape')
        if not np.isfinite(np.r_[poses.ravel(),times,quats.ravel()]).all():raise ValueError('Nonfinite coordinated plan')
        if times[0]!=0 or times[-1]<12 or np.any(np.diff(times)<=0) or not np.isclose(float(plan['depth_m']),.17,rtol=0,atol=1e-9):raise ValueError('Invalid coordinated stage depth/duration')
        if np.any(poses<self.limits[:,0]+.01) or np.any(poses>self.limits[:,1]-.01) or np.min(poses[:,[4,10]])<-.75-1e-8:raise ValueError('Coordinated joint margin exceeded')
        if np.max(abs(np.diff(poses,axis=0)/np.diff(times)[:,None]))>.25:raise ValueError('Coordinated target speed exceeded')
        if np.any(abs(np.linalg.norm(quats,axis=1)-1)>.01) or np.any(1-2*(quats[:,1]**2+quats[:,2]**2)<np.cos(np.radians(30))):raise ValueError('Coordinated pelvis path exceeds bounds')
        if np.max(abs(np.asarray(dq)))>.2 or np.max(abs(np.asarray(q)[:15]-poses[0,:15]))>.03:raise ValueError('本轮保持姿态已偏离协调计划，请重新生成')
        a=np.asarray(quat)/np.linalg.norm(quat)
        if abs(float(a@quats[0]))<np.cos(np.radians(2)/2):raise ValueError('当前骨盆方向与协调计划不一致')
        origin=self.pose_at(self.motion_time).copy()
        if np.max(abs(origin[:15]-poses[0,:15]))>.2:raise ValueError('协调计划起点与保持目标差异过大')
        self.stage_start=origin;self.poses=poses.copy();self.times=times.copy()
        self.motion_time=0.;self.motion_limit=float(times[-1]);self.trial_stage='measured_coordinated';self.phase='lowering';self.stage_hold_since=None

    def lower(self,now,q,dq):
        if self.phase!='prepared' or self.ready_since is None or now-self.ready_since<1:raise ValueError('准备姿态尚未稳定')
        if max(abs(np.asarray(q)-BASE))>.15 or max(abs(np.asarray(dq)))>.2:raise ValueError('请等待机器人站稳')
        self.phase='lowering';self.at=now;self.motion_time=0.;self.held=self.previous.copy()
        self.anchor=self.com0=self.last_com=None;self.velocity[:]=0

    def note_compensation_warning(self,now,values,q,dq,quat,gyro):
        """Record a historical diagnostic threshold; never change motion state."""
        values=np.asarray(values);joint=int(np.argmax(abs(values)))
        if self.compensation_event is None:
            self.compensation_event=dict(reason='support_compensation_warning',
                response='diagnostic_only',first_at=now,last_at=now,
                exceeding_samples=0,warning_threshold_nm=40.,motion_time_s=self.motion_time,
                joint=joint,joint_name=self.model.spec.get('joint_names',list(range(29)))[joint],
                first_requested_nm=float(values[joint]),peak_abs_nm=0.,
                q=np.asarray(q).tolist(),dq=np.asarray(dq).tolist(),
                quaternion=np.asarray(quat).tolist(),gyro=np.asarray(gyro).tolist())
        event=self.compensation_event
        event['last_at']=now;event['exceeding_samples']+=1
        if abs(values[joint])>event['peak_abs_nm']:
            event.update(peak_abs_nm=float(abs(values[joint])),peak_joint=joint,
                peak_joint_name=self.model.spec.get('joint_names',list(range(29)))[joint])

    def pause_progress(self,reason):
        """Latch a soft pause; continue feedback control, never auto-resume."""
        if self.phase in ('lowering','holding'):
            self.hold_target=self.pose_at(self.motion_time).copy()
            self.phase='paused'
            self.fault_context=dict(reason=reason,response='freeze_progress',motion_time_s=self.motion_time)

    def tick(self,now,q,dq,quat,gyro,heartbeat=True):
        try:
            return self._tick(now,q,dq,quat,gyro,heartbeat)
        except Exception as error:
            context=dict(self.fault_context or {})
            context.update(error=str(error),phase=self.phase,motion_time_s=self.motion_time,
                q=np.asarray(q).tolist(),dq=np.asarray(dq).tolist(),
                quaternion=np.asarray(quat).tolist(),gyro=np.asarray(gyro).tolist(),
                previous_command=None if self.previous is None else self.previous.tolist())
            self.fault_context=context
            raise

    def _tick(self,now,q,dq,quat,gyro,heartbeat=True):
        q=np.asarray(q);dq=np.asarray(dq);dt=now-self.last
        if dt<0 or dt>.05:raise ValueError('控制循环超时')
        self.last=now
        if not np.isfinite(np.r_[q,dq,quat,gyro]).all() or len(q)!=29:raise ValueError('无效反馈')
        if max(abs(dq))>4:
            j=int(np.argmax(abs(dq)))
            self.fault_context=dict(reason='joint_speed_limit',joint=j,
                joint_name=self.model.spec.get('joint_names',list(range(29)))[j],
                measured_rad_s=float(dq[j]),limit_rad_s=4.)
            raise ValueError('关节速度超过实验限制')
        if 1-2*(quat[1]**2+quat[2]**2)<np.cos(np.radians(35)):raise ValueError('骨盆倾斜超过实验限制')
        if np.any(q<self.limits[:,0]-.02) or np.any(q>self.limits[:,1]+.02):raise ValueError('关节反馈超限')
        result=self.model.calculate(q,dq,quat,gyro)
        raw_feedforward=result['feedforward'].copy()
        if np.max(abs(raw_feedforward))>40:
            self.note_compensation_warning(now,raw_feedforward,q,dq,quat,gyro)

        if self.anchor is None:self.anchor=result['feet'].mean(axis=0).copy()
        com=self.anchor-result['feet'].mean(axis=0)+result['com']
        if self.heading_rotation is None:
            r=rotation(quat);yaw=np.arctan2(r[1,0],r[0,0]);cs,sn=np.cos(yaw),np.sin(yaw)
            self.heading_rotation=np.array([[cs,sn,0],[-sn,cs,0],[0,0,1.]])
        if self.com0 is None:self.com0=com.copy();self.last_com=com.copy()
        if dt>0:self.velocity+=dt/(.02+dt)*((com-self.last_com)/dt-self.velocity)
        self.last_com=com.copy()
        com_error=com-self.com0
        feedback_velocity=self.velocity.copy()
        if self.heading_frame:
            com_error=self.heading_rotation@com_error;feedback_velocity=self.heading_rotation@feedback_velocity
        if not heartbeat and self.phase in ('preparing','lowering'):
            self.phase='paused';self.hold_target=self.pose_at(self.motion_time)
        if self.phase=='preparing':
            elapsed=now-self.at
            gain=smooth(elapsed/2);target=self.start+(BASE-self.start)*smooth((elapsed-2)/4)
            command=np.column_stack((target,np.zeros(29),gain*result['feedforward'],gain*KP,gain*KD))
            if elapsed>=6:self.phase='prepared'
        else:
            target=BASE.copy() if self.phase=='prepared' else self.pose_at(self.motion_limit)
            if self.phase=='lowering':
                self.motion_time=min(self.motion_time+dt,self.motion_limit)
                target=self.pose_at(self.motion_time)
                if self.motion_time>=self.motion_limit:self.phase='holding';self.stage_hold_since=now
            if self.phase in ('paused','limited_hold'):target=self.hold_target.copy()
            # Feedback is applied only to the lowering/holding trajectory.
            if self.phase in ('lowering','holding','paused','limited_hold'):
                target[[4,10]]+=np.clip(1.5*com_error[0]+.3*feedback_velocity[0],-.2,.2)
                target[[5,11]]+=np.clip(-1.5*com_error[1]-.3*feedback_velocity[1],-.15,.15)
            unconstrained_target=target.copy()
            target=np.clip(target,self.limits[:,0]+.01,self.limits[:,1]-.01);target[[4,10]]=np.maximum(target[[4,10]],-.8)
            target_velocity=self.velocity_at(self.motion_time) if self.phase=='lowering' else np.zeros(29)
            target_velocity[abs(target-unconstrained_target)>1e-9]=0.
            feedforward=result['feedforward']
            if self.support is not None:
                pd=KP*(target-q)+KD*(target_velocity-dq)
                self.support_diagnostics=self.support.solve(result,pd)
                feedforward=self.support_diagnostics['feedforward']
            command=np.column_stack((target,target_velocity,feedforward,KP,KD))
            if self.phase in ('lowering','holding','limited_hold','paused') and hasattr(self,'held'):command=self.held+(command-self.held)*smooth((now-self.at)/self.handoff_seconds)
        if self.previous is not None:
            command[:,0]=np.clip(command[:,0],self.previous[:,0]-1.5*dt,self.previous[:,0]+1.5*dt)
            command[:,2]=np.clip(command[:,2],self.previous[:,2]-100*dt,self.previous[:,2]+100*dt)
        if max(abs(command[:,2]))>40 and np.max(abs(raw_feedforward))<=40:
            self.note_compensation_warning(now,command[:,2],q,dq,quat,gyro)
        requested=command.copy()
        if self.recovery_enabled:
            # Simulation candidate only: cap feedforward without discarding PD.
            ff_limit=self.effort_limits
            command[:,2]=np.clip(command[:,2],-ff_limit,ff_limit)
            pd=command[:,3]*(command[:,0]-q)+command[:,4]*(command[:,1]-dq)
            total=command[:,2]+pd
            scale=np.minimum(1.,self.effort_limits/np.maximum(abs(total),1e-12))
            command[:,2]*=scale;command[:,3]*=scale;command[:,4]*=scale
            # Bound is for this feedback sample, not a hardware torque guarantee.
        pd=command[:,3]*(command[:,0]-q)+command[:,4]*(command[:,1]-dq)
        self.diagnostics=dict(motion_time_s=self.motion_time,phase=self.phase,
            compensation_event=None if self.compensation_event is None else dict(self.compensation_event),
            raw_feedforward_nm=raw_feedforward,requested_command=requested,support=self.support_diagnostics,
            estimated_pd_nm=pd.copy(),estimated_total_target_nm=(command[:,2]+pd).copy(),
            com_estimate=com.copy(),com_velocity_estimate=self.velocity.copy(),heading_com_error=com_error.copy(),heading_com_velocity=feedback_velocity.copy())
        if self.phase!='preparing' and np.any(abs(q-command[:,0])>(.4+self.entry_error*(1-smooth((now-self.at)/1.)))):raise ValueError('关节跟踪误差超过实验限制')
        stable=self.phase=='prepared' and max(abs(q-BASE))<=.15 and max(abs(dq))<=.2
        if not stable:self.ready_since=None
        elif self.ready_since is None:self.ready_since=now
        self.previous=command.copy();return command
