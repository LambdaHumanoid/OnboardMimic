"""Compare the standalone estimator to MuJoCo on the workstation."""
import argparse,json,time
from pathlib import Path
import mujoco
import numpy as np
from native_model import NativeModel as Model
from model import rotation
p=argparse.ArgumentParser();p.add_argument('xml');p.add_argument('--samples',type=int,default=100);a=p.parse_args()
model=Model(Path(__file__).with_name('model.json'));m=mujoco.MjModel.from_xml_path(a.xml);d=mujoco.MjData(m)
js=[m.joint(n+'_joint').id for n in model.spec['joint_names']];qa=m.jnt_qposadr[js];va=m.jnt_dofadr[js]
rng=np.random.default_rng(16);errors={k:0. for k in ('com','feet','feedforward','bias','jacobian','foot_rotations')};elapsed=[]
for n in range(a.samples):
    q=rng.uniform(-.4,.4,29);dq=rng.normal(0,.5,29);quat=rng.normal(0,1,4);quat/=np.linalg.norm(quat);gyro=rng.normal(0,.3,3)
    d.qpos[:]=0;d.qpos[3:7]=quat;d.qpos[qa]=q;d.qvel[:]=0;d.qvel[3:6]=gyro;d.qvel[va]=dq;mujoco.mj_forward(m,d)
    jac=[]
    for foot in model.spec['feet']:
        jp=np.zeros((3,m.nv));jr=jp.copy();mujoco.mj_jacBody(m,d,jp,jr,foot);jac.extend([jp,jr])
    jac=np.vstack(jac);w=np.linalg.lstsq(jac[:,:6].T,d.qfrc_bias[:6],rcond=None)[0]
    ff=d.qfrc_bias[va]-jac[:,va].T@w
    start=time.perf_counter();actual=model.calculate(q,dq,quat,gyro);elapsed.append(time.perf_counter()-start)
    expected=dict(com=d.subtree_com[1],feet=d.xpos[model.spec['feet']],feedforward=ff,
        bias=np.r_[d.qfrc_bias[:3],rotation(quat)@d.qfrc_bias[3:6],d.qfrc_bias[va]],
        jacobian=np.hstack((jac[:,:3],jac[:,3:6]@rotation(quat).T,jac[:,va])),
        foot_rotations=d.xmat[model.spec['feet']].reshape(2,3,3))
    for key in errors:errors[key]=max(errors[key],float(np.max(abs(actual[key]-expected[key]))))
print(json.dumps(dict(samples=a.samples,max_errors=errors,mean_ms=np.mean(elapsed)*1000,max_ms=max(elapsed)*1000),indent=2))
assert max(errors.values())<1e-8,errors
