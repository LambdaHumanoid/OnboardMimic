"""Offline falsification probe for the experimental joint-space stop.
No robot libraries, controller processes, DDS, sockets or network access.
This is NOT a SONIC closed-loop rollout or a physical acceptance test.
"""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import numpy as np
import mujoco

ROOT=Path(__file__).resolve().parents[2]
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory() as tmp:
        library=Path(tmp)/'stop.so'
        subprocess.run(['g++','-shared','-fPIC','-std=c++17','-O2',
          '-I'+str(ROOT/'onboardmimic/robots/unitree_g1/native_bridge'),
          '-I'+str(ROOT/'onboardmimic/backends/sonic/deploy/src/g1/g1_deploy_onnx_ref/include'),
          str(ROOT/'tests/simulation/controlled_stop_shim.cpp'),'-o',str(library)],check=True)
        lib=ctypes.CDLL(str(library));ptr=np.ctypeslib.ndpointer(dtype=np.float32,flags='C_CONTIGUOUS')
        lib.stop_create.restype=ctypes.c_void_p
        lib.stop_destroy.argtypes=[ctypes.c_void_p]
        lib.stop_step.argtypes=[ctypes.c_void_p,ctypes.c_double,ptr,ptr,ctypes.c_float,ctypes.c_int,ctypes.c_int,ptr]
        lib.stop_reason.argtypes=[ctypes.c_void_p];lib.stop_reason.restype=ctypes.c_char_p
        names=[s+'_'+n for s in ('left','right') for n in ('hip_pitch','hip_roll','hip_yaw','knee','ankle_pitch','ankle_roll')]+['waist_yaw','waist_roll','waist_pitch']+[s+'_'+n for s in ('left','right') for n in ('shoulder_pitch','shoulder_roll','shoulder_yaw','elbow','wrist_roll','wrist_pitch','wrist_yaw')]
        model=mujoco.MjModel.from_xml_path(str(args.model));model.opt.timestep=.002
        joints=[model.joint(n+'_joint').id for n in names]
        qa=model.jnt_qposadr[joints];va=model.jnt_dofadr[joints]
        actuators=np.asarray([list(model.actuator_trnid[:,0]).index(j) for j in joints])
        torso=model.body('torso_link').id
        feet=[model.body(s+'_ankle_roll_link').id for s in ('left','right')]
        q0=np.array(json.loads((ROOT/'onboardmimic/backends/sonic/deploy/onboard/baseline_q.json').read_text()))
        results=[]
        for label,velocity,angle in [('standing',0,0),('moving_forward',.3,0),('moving_sideways',.2,0),('tilted',0,.2)]:
            data=mujoco.MjData(model);data.qpos[:7]=[0,0,.78874,1,0,0,0];data.qpos[qa]=q0
            mujoco.mj_forward(model,data)
            # Ideal start: move the feet down to ground contact, with zero joint velocities.
            # This favorable initialization is not a policy-generated stance.
            data.qpos[2]-=min(data.geom_xpos[g,2]-model.geom_size[g,0] for g in range(model.ngeom) if model.geom_bodyid[g] in feet and model.geom_type[g]==mujoco.mjtGeom.mjGEOM_SPHERE)
            data.qpos[2]-=.0005
            if label=='moving_forward':data.qvel[0]=velocity
            if label=='moving_sideways':data.qvel[1]=velocity
            if angle:data.qpos[3:7]=[np.cos(angle/2),0,np.sin(angle/2),0]
            mujoco.mj_forward(model,data)
            handle=lib.stop_create();out=np.zeros((29,5),np.float32);trace=[]
            first_abort=None;fall=None
            for step in range(2500):
                t=step*.002
                w,x,y,z=data.xquat[torso];tilt=float(np.arccos(np.clip(1-2*(x*x+y*y),-1,1)))
                contacts=set()
                for con in data.contact:
                    bodies={int(model.geom_bodyid[con.geom1]),int(model.geom_bodyid[con.geom2])}
                    if 0 in bodies:contacts|=bodies
                support=all(f in contacts for f in feet)
                phase=lib.stop_step(handle,t,np.asarray(data.qpos[qa],np.float32),np.asarray(data.qvel[va],np.float32),tilt,int(support),int(step==0),out)
                if phase==4 and first_abort is None:first_abort=dict(time=t,reason=lib.stop_reason(handle).decode())
                if (tilt>.785 or data.qpos[2]<.3) and fall is None:fall=t
                q,dq,tau,kp,kd=out.T
                data.ctrl[actuators]=np.clip(tau+kp*(q-data.qpos[qa])+kd*(dq-data.qvel[va]),model.actuator_ctrlrange[actuators,0],model.actuator_ctrlrange[actuators,1])
                trace.append([t,float(data.qpos[2]),float(np.degrees(tilt)),phase,int(support),float(np.max(np.abs(data.qvel[va])))])
                mujoco.mj_step(model,data)
                if not np.isfinite(data.qpos).all():raise RuntimeError('nonfinite physics')
            lib.stop_destroy(handle)
            np.savetxt(args.output/(label+'.csv'),trace,delimiter=',',header='seconds,base_z,tilt_deg,phase,two_feet_contact,max_joint_speed',comments='')
            results.append(dict(case=label,abort=first_abort,fall_seconds=fall,final_phase=phase,min_base_z=min(x[1] for x in trace),max_tilt_deg=max(x[2] for x in trace)))
        report=dict(scope='Offline candidate falsification, no SONIC and no hardware',model=str(args.model),model_sha256=hashlib.sha256(args.model.read_bytes()).hexdigest(),cases=results,hardware_ready=False)
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
if __name__=='__main__':main()
