"""Opt-in direct six-second handoff controller, sharing the existing protections."""
import numpy as np
from scipy.interpolate import BSpline
from controller import Controller


class DirectTrajectory:
    def __init__(self,plan,limits):
        self.path=BSpline(plan['spline_knots'],plan['spline_coefficients'],int(plan['spline_degree']))
        self.clock=BSpline(plan['clock_knots'],plan['clock_coefficients'],int(plan['clock_degree']))
        self.path_d=self.path.derivative(1);self.clock_d=self.clock.derivative(1)
        self.initial_quaternion=np.asarray(plan['initial_quaternion'])
        t=np.linspace(0,6,1501);s=self.clock(t);sd=self.clock_d(t);q=self.path(s)[:,:29]
        dq=self.path_d(s)[:,:29]*sd[:,None]
        ddq=self.path.derivative(2)(s)[:,:29]*sd[:,None]**2+self.path_d(s)[:,:29]*self.clock.derivative(2)(t)[:,None]
        if not np.isfinite(np.r_[q.ravel(),dq.ravel(),ddq.ravel(),self.initial_quaternion]).all():raise ValueError('Invalid direct plan values')
        if abs(float(plan['depth_m'])-.20)>1e-9 or abs(float(plan['time'][-1])-6)>1e-9:raise ValueError('Invalid direct duration/depth')
        if abs(float(s[0]))>1e-8 or abs(float(s[-1])-1)>1e-8 or np.min(sd)<-1e-8:raise ValueError('Invalid direct clock')
        if np.max(abs(dq))>1.2 or np.max(abs(ddq))>4.:raise ValueError('Direct derivative admission failed')
        if np.any(q<limits[:,0]+.005) or np.any(q>limits[:,1]-.005) or np.min(q[:,[4,10]])<-.755:raise ValueError('Direct joint admission failed')
        if np.max(abs(dq[[0,-1]]))>1e-8 or np.max(abs(ddq[[0,-1]]))>1e-7:raise ValueError('Direct endpoints are not at rest')
        self.origin=q[0].copy()

    def sample(self,t):
        t=float(np.clip(t,0,6));s=float(self.clock(t));return self.path(s)[:29].copy(),self.path_d(s)[:29]*float(self.clock_d(t))


class DirectController(Controller):
    @classmethod
    def direct_handoff(cls,model,plan,q,dq,quat,command,now,prepared=None):
        trajectory=prepared if prepared is not None else DirectTrajectory(plan,np.asarray(model.spec['limits']))
        if np.max(abs(np.asarray(q)[:15]-trajectory.origin[:15]))>.08:raise ValueError('站姿与本轮计划不一致，请重新生成')
        a=np.asarray(quat)/np.linalg.norm(quat);b=trajectory.initial_quaternion
        if b.shape!=(4,) or abs(np.linalg.norm(b)-1)>.01 or abs(float(a@b))<np.cos(np.radians(2)/2):raise ValueError('骨盆方向与计划不一致')
        c=cls.handoff(model,plan,q,dq,command,now,recovery_enabled=True,trial_stage='handoff_hold')
        c.direct=trajectory;c.trial_stage='direct_six';c.motion_limit=6.;c.offset=c.start-trajectory.origin
        return c

    def pose_at(self,t):
        if not hasattr(self,'direct'):return super().pose_at(t)
        q,_=self.direct.sample(t);u=float(np.clip(t,0,1));w=10*u**3-15*u**4+6*u**5
        q+=self.offset*(1-w);q[15:]=self.start[15:];return q

    def velocity_at(self,t):
        _,dq=self.direct.sample(t);u=float(np.clip(t,0,1));dw=30*u**2-60*u**3+30*u**4
        dq-=self.offset*dw;dq[15:]=0.;return dq
