"""Run on the simulation workstation only; export inertial geometry, not meshes."""
import argparse,json
import mujoco
import numpy as np
p=argparse.ArgumentParser();p.add_argument('model');p.add_argument('output');a=p.parse_args()
m=mujoco.MjModel.from_xml_path(a.model)
assert np.allclose(m.opt.gravity,[0,0,-9.81])
assert sum(m.jnt_type==mujoco.mjtJoint.mjJNT_FREE)==1
names=[s+'_'+n for s in ('left','right') for n in ('hip_pitch','hip_roll','hip_yaw','knee','ankle_pitch','ankle_roll')]+['waist_yaw','waist_roll','waist_pitch']+[s+'_'+n for s in ('left','right') for n in ('shoulder_pitch','shoulder_roll','shoulder_yaw','elbow','wrist_roll','wrist_pitch','wrist_yaw')]
js=[m.joint(n+'_joint').id for n in names]
bodies=[]
for i in range(m.nbody):
    joints=list(range(int(m.body_jntadr[i]),int(m.body_jntadr[i]+m.body_jntnum[i])))
    assert len(joints)<=1
    j=joints[0] if joints else None
    free=j is not None and m.jnt_type[j]==mujoco.mjtJoint.mjJNT_FREE
    if j is not None and not free:
        assert m.jnt_type[j]==mujoco.mjtJoint.mjJNT_HINGE and np.allclose(m.jnt_pos[j],0)
        assert m.qpos0[m.jnt_qposadr[j]]==0
    bodies.append(dict(parent=int(m.body_parentid[i]),pos=m.body_pos[i].tolist(),quat=m.body_quat[i].tolist(),mass=float(m.body_mass[i]),ipos=m.body_ipos[i].tolist(),iquat=m.body_iquat[i].tolist(),inertia=m.body_inertia[i].tolist(),free=bool(free),joint=js.index(j) if j in js else None,axis=m.jnt_axis[j].tolist() if j in js else None))
act=[list(m.actuator_trnid[:,0]).index(j) for j in js]
spec=dict(simulation_effort_limits_nm=np.minimum(abs(m.actuator_ctrlrange[act,0]),abs(m.actuator_ctrlrange[act,1])).tolist(),bodies=bodies,feet=[m.body(s+'_ankle_roll_link').id for s in ('left','right')],gravity=m.opt.gravity.tolist(),joint_names=names,limits=m.jnt_range[js].tolist())
open(a.output,'w').write(json.dumps(spec,indent=2)+'\n')
