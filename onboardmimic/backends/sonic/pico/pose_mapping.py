"""CPU mapping for the imported SONIC SMPL pathway. No network or SDK."""
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation as R
from onboardmimic.backends.sonic.pico.wrist_mapping import compute_wrist_joints

PARENTS=[-1,0,0,0,1,2,3,4,5,6,7,8,9,9,9,12,13,14,16,17,18,19,20,22]
REST=json.loads(Path(__file__).with_name('human_rest.json').read_text())
J=np.asarray(REST['J'],dtype=float);FK_PARENTS=REST['parents']
BASE=R.from_quat([.5,.5,.5,.5]);UP=R.from_euler('x',90,degrees=True);FLIP=R.from_euler('y',180,degrees=True)

def map_body(poses):
    p=np.asarray(poses,dtype=float)
    if p.shape!=(24,7) or not np.isfinite(p).all():raise ValueError('无效 PICO 骨架')
    norm=np.linalg.norm(p[:,3:],axis=1)
    if np.any(norm<.8) or np.any(norm>1.2):raise ValueError('无效姿态四元数')
    g=R.from_quat(p[:,3:])*FLIP
    local=g[np.asarray(PARENTS[1:22])].inv()*g[1:22]
    body=local.as_rotvec().astype(np.float32)
    # Preserves the imported forward kinematics; batched conversion avoids per-joint SciPy objects on Orin.
    root=UP*g[0]
    def matrix(r):return r.as_matrix() if hasattr(r,'as_matrix') else r.as_dcm()
    local_m=np.repeat(np.eye(3)[None],55,axis=0)
    local_m[0]=matrix(root);local_m[1:22]=matrix(local)
    world_m=np.empty((55,3,3));world_p=np.empty((55,3))
    for i,parent in enumerate(FK_PARENTS):
        if parent<0:world_m[i]=local_m[i];world_p[i]=J[i]
        else:
            world_m[i]=world_m[parent]@local_m[i]
            world_p[i]=world_p[parent]+world_m[parent]@(J[i]-J[parent])
    orient=root*BASE.inv();indices=list(range(22))+[39,54]
    joints=orient.inv().apply(world_p[indices]).astype(np.float32)
    left,right=compute_wrist_joints(body.reshape(63));q=np.zeros(29,dtype=np.float32)
    q[[23,25,27]]=left;q[[24,26,28]]=right
    quat=orient.as_quat()[[3,0,1,2]].astype(np.float32)
    return dict(smpl_pose=body,smpl_joints=joints,body_quat_w=quat,joint_pos=q,joint_vel=np.zeros(29,dtype=np.float32))

def mix(a,b,t):
    # Interpolate pose rotations, local joints, and normalized root quaternion.
    ra=R.from_rotvec(a['smpl_pose']);rb=R.from_rotvec(b['smpl_pose'])
    aqs=ra.as_quat();bqs=rb.as_quat();bqs[np.sum(aqs*bqs,axis=1)<0]*=-1
    qs=(1-t)*aqs+t*bqs;qs/=np.linalg.norm(qs,axis=1)[:,None]
    pose=R.from_quat(qs).as_rotvec().astype(np.float32)
    qa=a['body_quat_w'];qb=b['body_quat_w'];qb=qb if np.dot(qa,qb)>=0 else -qb
    quat=(1-t)*qa+t*qb;quat/=np.linalg.norm(quat)
    left,right=compute_wrist_joints(pose.reshape(63));q=np.zeros(29,dtype=np.float32);q[[23,25,27]]=left;q[[24,26,28]]=right
    return dict(smpl_pose=pose,smpl_joints=((1-t)*a['smpl_joints']+t*b['smpl_joints']).astype(np.float32),body_quat_w=quat.astype(np.float32),joint_pos=q,joint_vel=np.zeros(29,dtype=np.float32))
