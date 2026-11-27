"""Simulation-only constrained contact-wrench allocation.

All constraints depend on an assumed flat, two-foot contact state. They are not
contact measurements or hardware limits. Never used by the physical worker.
"""
import numpy as np
from scipy.optimize import minimize
from model import skew

class InfeasibleSupport(ValueError):
    pass

class SupportAllocator:
    def __init__(self,limits,friction=.4):
        self.limits=np.asarray(limits,dtype=float)
        if self.limits.shape!=(29,) or not np.isfinite(self.limits).all() or np.any(self.limits<=0):raise ValueError('Invalid model effort bounds')
        self.friction=friction;self.previous=None
        self.scale=np.tile([200.,200.,200.,20.,20.,20.],2)

    def solve(self,result,pd):
        bias=np.asarray(result['bias']);jac=np.asarray(result['jacobian']);pd=np.asarray(pd)
        rotations=np.asarray(result['foot_rotations']);feet=np.asarray(result['feet'])
        if not all(np.isfinite(x).all() for x in (bias,jac,pd,rotations,feet)):raise InfeasibleSupport('nonfinite contact model')
        if np.any(rotations[:,2,2]<np.cos(np.radians(12))) or abs(feet[0,2]-feet[1,2])>.025:
            raise InfeasibleSupport('assumed flat two-foot contact is inconsistent with geometry')
        A=jac[:,:6].T;b=bias[:6];B=jac[:,6:].T
        rows=[];bounds=[]
        def upper(row,value=0.):rows.append(row);bounds.append(value)
        for foot in range(2):
            transform=np.zeros((6,12));rotation=rotations[foot].T
            transform[:3,foot*6:foot*6+3]=rotation
            transform[3:,foot*6+3:foot*6+6]=rotation
            # Move wrench from ankle origin to the sole plane (local z=-.035m).
            transform[3:,foot*6:foot*6+3]=-skew(np.array([0.,0.,-.035]))@rotation
            fx,fy,fz,mx,my,mz=transform
            upper(-fz)
            for axis in (fx,fy):upper(axis-self.friction*fz);upper(-axis-self.friction*fz)
            # COP rectangle x=[-.05,.12], y=[-.025,.025].
            upper(mx-.025*fz);upper(-mx-.025*fz)
            upper(my-.05*fz);upper(-my-.12*fz)
            upper(mz-.02*fz);upper(-mz-.02*fz)
        ff_limit=self.limits
        for j in range(29):
            # ff = bias_joint - J_joint^T wrench; also constrain ff + PD.
            upper(-B[j],ff_limit[j]-bias[6+j]);upper(B[j],ff_limit[j]+bias[6+j])
            upper(-B[j],self.limits[j]-bias[6+j]-pd[j]);upper(B[j],self.limits[j]+bias[6+j]+pd[j])
        G=np.asarray(rows);h=np.asarray(bounds)
        E=A*self.scale;en=np.maximum(np.linalg.norm(E,axis=1),1.);E=E/en[:,None];target=b/en
        C=G*self.scale;cn=np.maximum(np.linalg.norm(C,axis=1),1.);C=C/cn[:,None];d=h/cn
        start=np.linalg.lstsq(E,target,rcond=None)[0] if self.previous is None else self.previous/self.scale
        res=minimize(lambda x:.5*np.dot(x,x),start,jac=lambda x:x,method='SLSQP',
            constraints=[dict(type='eq',fun=lambda x:E@x-target,jac=lambda x:E),dict(type='ineq',fun=lambda x:d-C@x,jac=lambda x:-C)],
            options=dict(maxiter=60,ftol=1e-9,disp=False))
        f=res.x*self.scale;ff=bias[6:]-B@f
        eq=float(np.max(abs(A@f-b)));violation=float(np.max(G@f-h))
        if not res.success or eq>1e-5 or violation>1e-5:
            raise InfeasibleSupport('no feasible bounded support: '+str(res.message))
        self.previous=f.copy()
        return dict(feedforward=ff,wrench=f,base_residual=eq,max_inequality_violation=violation,iterations=int(res.nit))
