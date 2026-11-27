"""Read-only rigid-body calculations. No simulator, transport or motor output."""
import json
import numpy as np


def rotation(q):
    w, x, y, z = np.asarray(q) / np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def skew(v):
    x,y,z=v
    return np.array([[0,-z,y],[z,0,-x],[-y,x,0]])


class Model:
    def __init__(self, path):
        self.spec=json.loads(open(path).read())
        self.bodies=self.spec['bodies']
        self.n=len(self.bodies)
        self.fixed=[rotation(b['quat']) for b in self.bodies]
        self.inertia=[rotation(b['iquat']) @ np.diag(b['inertia']) @ rotation(b['iquat']).T for b in self.bodies]
        self.mass=sum(b['mass'] for b in self.bodies)

    def calculate(self, q, dq, quat, gyro):
        q=np.asarray(q);dq=np.asarray(dq)
        if q.shape!=(29,) or dq.shape!=(29,) or not np.isfinite(np.r_[q,dq,quat,gyro]).all():
            raise ValueError('Invalid measured state')
        R=[np.eye(3) for _ in self.bodies]
        pos=np.zeros((self.n,3));omega=pos.copy();alpha=pos.copy();acc=pos.copy()
        jv=np.zeros((self.n,3,35));jw=jv.copy()
        bias=np.zeros(35);com=np.zeros(3)
        for i,b in enumerate(self.bodies):
            if i==0:continue
            p=b['parent'];r=R[p]@np.array(b['pos'])
            pos[i]=pos[p]+r
            R[i]=R[p]@self.fixed[i]
            omega[i]=omega[p];alpha[i]=alpha[p]
            acc[i]=acc[p]+np.cross(alpha[p],r)+np.cross(omega[p],np.cross(omega[p],r))
            jv[i]=jv[p]-skew(r)@jw[p];jw[i]=jw[p]
            if b['free']:
                pos[i]=0
                R[i]=rotation(quat);omega[i]=R[i]@gyro
                jv[i,:,:3]=np.eye(3);jw[i,:,3:6]=np.eye(3)
            j=b['joint']
            if j is not None:
                axis=R[i]@np.array(b['axis']);k=skew(np.array(b['axis']))
                R[i]=R[i]@(np.eye(3)+np.sin(q[j])*k+(1-np.cos(q[j]))*(k@k))
                alpha[i]+=np.cross(omega[p],axis*dq[j]);omega[i]+=axis*dq[j]
                jw[i,:,6+j]=axis
            offset=R[i]@np.array(b['ipos']);center=pos[i]+offset
            center_jac=jv[i]-skew(offset)@jw[i]
            linear=acc[i]+np.cross(alpha[i],offset)+np.cross(omega[i],np.cross(omega[i],offset))
            force=b['mass']*(linear-np.array(self.spec['gravity']))
            inertia=R[i]@self.inertia[i]@R[i].T
            torque=inertia@alpha[i]+np.cross(omega[i],inertia@omega[i])
            bias+=center_jac.T@force+jw[i].T@torque
            com+=b['mass']*center
        feet=self.spec['feet'];jac=np.vstack([x for i in feet for x in (jv[i],jw[i])])
        wrench=np.linalg.lstsq(jac[:,:6].T,bias[:6],rcond=None)[0]
        return dict(com=com/self.mass,feet=pos[feet],bias=bias,jacobian=jac,foot_rotations=np.asarray([R[i] for i in feet]),feedforward=bias[6:]-jac[:,6:].T@wrench)
