"""Run the native protection executor with measured-joint/IMU inputs only.
Contacts are read for reporting, never supplied to the executor. No transport.
"""
import argparse,ctypes,hashlib,json,subprocess,sys,tempfile
from pathlib import Path
import numpy as np
import mujoco
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from experiments.controlled_kneel.simulate import setup,impact_samples,contact_info

def run(lib,case,out):
    m,d,qa,va,act,limits,cp,names=setup()
    if case.get('checkpoint'):
        cp=np.load(case['checkpoint'])
        d.qpos[:]=cp['qpos'];d.qvel[:]=cp['qvel'];d.qacc_warmstart[:]=cp['qacc_warmstart']
    for joint,speed in case.get('joint_speeds',{}).items():d.qvel[va[int(joint)]]=speed
    d.qvel[0]+=case.get('vx',0. if case.get('checkpoint') else .1)
    d.qvel[1]+=case.get('vy',0.)
    if case.get('pitch_offset'):
        angle=case['pitch_offset'];rotation=np.array([np.cos(angle/2),0,np.sin(angle/2),0])
        original=d.qpos[3:7].copy();mujoco.mju_mulQuat(d.qpos[3:7],rotation,original)
    d.qvel[4]+=case.get('pitch_rate',0.)
    m.geom_friction[:,0]=case.get('friction',.8)
    mujoco.mj_forward(m,d)
    ranges=m.jnt_range[[m.joint(n+'_joint').id for n in names]]
    initial_state=dict(max_joint_speed=float(np.max(abs(d.qvel[va]))),root_velocity=d.qvel[:6].tolist(),left_right_knee_difference=float(d.qpos[qa[3]]-d.qpos[qa[9]]))
    if hasattr(lib,'protection_faults'):
        lib.protection_faults.argtypes=[ctypes.c_uint64,ctypes.c_int,ctypes.c_double]
        lib.protection_faults(sum(1<<i for i in case.get('failed_joints',[])),case.get('fault_kind',0),case.get('fault_at',.02))
    handle=lib.protection_create();lib.protection_tune(handle,case.get('ankle_bias',.5));source=np.asarray(cp['command'],np.float32).reshape(-1)
    output=np.zeros((29,5),np.float32)
    bias_reader=getattr(lib,'protection_bias',None)
    if bias_reader is not None:
        bias_reader.argtypes=[ctypes.c_void_p];bias_reader.restype=ctypes.c_double
    strategy_reader=getattr(lib,'protection_strategy',None)
    if strategy_reader is not None:
        strategy_reader.argtypes=[ctypes.c_void_p];strategy_reader.restype=ctypes.c_char_p
    history=[];trace=[];frames=[];impact={};takeover=None;abort=None;maxspeed=0.;max_torque=0.;max_joint_limit_excess=0.;previous_phase=0;transitions=[]
    torso=m.body('torso_link').id;pelvis=m.body('pelvis').id
    knees=[m.body(side+'_knee_link').id for side in ('left','right')]
    hips=[m.body(side+'_hip_pitch_link').id for side in ('left','right')]
    butt_contact=None;pelvis_peak=0.;butt_speed=0.;geometry=[];self_penetration=0.
    delay=case.get('feedback_delay',0.)
    try:
        for step in range(4500):
            max_joint_limit_excess=max(max_joint_limit_excess,float(np.max(np.maximum(ranges[:,0]-d.qpos[qa],d.qpos[qa]-ranges[:,1]))))
            t=step*.002
            impulse=case.get('speed_injection')
            if impulse and abs(t-impulse['at'])<.001:
                for joint,speed in impulse['joints'].items():d.qvel[va[int(joint)]]=speed
                mujoco.mj_forward(m,d)
            quat=d.qpos[3:7];w,x,y,z=quat
            roll=float(np.arctan2(2*(w*x+y*z),1-2*(x*x+y*y)))
            pitch=float(np.arcsin(np.clip(2*(w*y-z*x),-1,1)))
            history.append((t,np.array(d.qpos[qa],np.float32),np.array(d.qvel[va],np.float32),roll,pitch))
            sample=history[max(0,step-round(delay/.002))]
            captured=sample[0]
            if case.get('lost_state') is not None and t>=case['lost_state']:
                sample=history[round(case['lost_state']/.002)];captured=sample[0]
            action=1 if step==0 else (2 if case.get('exit_at') is not None and abs(t-case['exit_at'])<.001 else 0)
            if case.get('cancel_at') is not None and abs(t-case['cancel_at'])<.001:action=3
            phase=lib.protection_step(handle,t,captured,sample[1],sample[2],sample[3],sample[4],source,action,output)
            if phase!=previous_phase:
                transitions.append(dict(time=t,phase=phase,reason=lib.protection_reason(handle).decode()));previous_phase=phase
            if phase==1 and takeover is None:takeover=t
            if phase==3 and abort is None:abort=dict(time=t,reason=lib.protection_reason(handle).decode())
            q,dq,tau,kp,kd=output.T
            torque=np.clip(tau+kp*(q-d.qpos[qa])+kd*(dq-d.qvel[va]),-limits,limits)
            if case.get('baseline'):torque=np.zeros(29)
            if case.get('fault_kind') and t>=case.get('fault_at',.02):
                # Loss of active drive, not a model of mechanical seizure/heat.
                torque[case['failed_joints']]=0.
                if not case.get('old_fault_baseline'):
                    assert np.all(output[case['failed_joints']]==0), 'Faulted motor still commanded'
            d.ctrl[act]=torque
            maxspeed=max(maxspeed,float(np.max(abs(d.qvel[va]))));max_torque=max(max_torque,float(np.max(abs(torque)/limits)))
            for region,s in impact_samples(m,d).items():
                row=impact.setdefault(region,dict(peak_force_n=0.,first_contact_s=None,impulse_first_100ms_ns=0.))
                row['peak_force_n']=max(row['peak_force_n'],s['force_n'])
                if s['force_n']>1 and row['first_contact_s'] is None:row['first_contact_s']=t
                if row['first_contact_s'] is not None and t<row['first_contact_s']+.1:row['impulse_first_100ms_ns']+=s['force_n']*.002
            self_penetration=max(self_penetration,-contact_info(m,d)[3])
            for contact_index,con in enumerate(d.contact):
                bodies=[int(m.geom_bodyid[g]) for g in (con.geom1,con.geom2)]
                if 0 not in bodies or pelvis not in bodies:continue
                force=np.zeros(6);mujoco.mj_contactForce(m,d,contact_index,force)
                pelvis_peak=max(pelvis_peak,float(force[0]))
                local=d.xmat[pelvis].reshape(3,3).T@(con.pos-d.xpos[pelvis])
                if force[0]>5 and local[0]<0 and butt_contact is None:
                    jp=np.zeros((3,m.nv));jr=np.zeros((3,m.nv));mujoco.mj_jac(m,d,jp,jr,con.pos,pelvis)
                    butt_speed=max(0.,-float((jp@d.qvel)[2]))
                    butt_contact=dict(time=t,pelvis_z=float(d.xpos[pelvis,2]),downward_speed=butt_speed)
            if step%10==0:
                R=d.xmat[torso].reshape(3,3);chest=d.xpos[torso]+R@np.array([.08,0,.16])
                knee=d.xpos[knees].mean(axis=0);hip=d.xpos[hips].mean(axis=0)
                thigh=knee-hip;thigh/=np.linalg.norm(thigh)
                geometry.append(dict(time=t,chest_knee_distance=float(np.linalg.norm(chest-knee)),torso_thigh_angle_deg=float(np.degrees(np.arccos(np.clip(R[:,2]@thigh,-1,1)))),torso_pitch_deg=float(np.degrees(np.arctan2(R[0,2],R[2,2]))),pelvis_z=float(d.xpos[pelvis,2])))
                frames.append(d.qpos.copy());trace.append(dict(time=t,phase=phase,z=float(d.qpos[2]),pitch=pitch,max_joint_speed=float(np.max(abs(d.qvel[va])))))
            mujoco.mj_step(m,d)
            if not np.isfinite(d.qpos).all():raise ValueError('Nonfinite dynamics')
        result=dict(effective_ankle_bias=bias_reader(handle) if bias_reader else case.get('ankle_bias',.5),self_penetration_m=self_penetration,butt_contact=butt_contact,pelvis_peak_force_n=pelvis_peak,case=case,takeover_s=takeover,abort=abort,transitions=transitions,impact=impact,max_joint_speed=maxspeed,max_torque_ratio=max_torque,final_z=float(d.qpos[2]),final_speed=float(np.max(abs(d.qvel))),final_contacts=contact_info(m,d)[0],hardware_ready=False)
        out.mkdir(parents=True,exist_ok=True)
        result['initial_state']=initial_state
        result['max_joint_limit_excess_rad']=max_joint_limit_excess
        brake_reader=getattr(lib,'protection_brake_events',None)
        if brake_reader is not None:
            brake_reader.argtypes=[ctypes.c_void_p];brake_reader.restype=ctypes.c_double;result['speed_brake_events']=int(brake_reader(handle))
        if strategy_reader is not None:
            result['strategy']=strategy_reader(handle).decode()
            for function,key in [('protection_entry_speed','entry_max_speed'),('protection_foot_difference','foot_height_difference')]:
                reader=getattr(lib,function);reader.argtypes=[ctypes.c_void_p];reader.restype=ctypes.c_double;result[key]=reader(handle)
        (out/'geometry.json').write_text(json.dumps(geometry));(out/'report.json').write_text(json.dumps(result,indent=2));(out/'trace.json').write_text(json.dumps(trace));np.save(out/'qpos.npy',frames)
        return result
    finally:lib.protection_destroy(handle)

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--cases',type=Path);a=p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        so=Path(tmp)/'protection.so'
        subprocess.run(['g++','-std=c++17','-shared','-fPIC','-O2','-I'+str(ROOT/'onboardmimic/robots/unitree_g1/native_bridge'),str(ROOT/'tests/simulation/protection_roll_shim.cpp'),'-o',str(so)],check=True)
        lib=ctypes.CDLL(str(so));ptr=np.ctypeslib.ndpointer(dtype=np.float32,flags='C_CONTIGUOUS')
        lib.protection_create.restype=ctypes.c_void_p;lib.protection_tune.argtypes=[ctypes.c_void_p,ctypes.c_double];lib.protection_destroy.argtypes=[ctypes.c_void_p]
        lib.protection_step.argtypes=[ctypes.c_void_p,ctypes.c_double,ctypes.c_double,ptr,ptr,ctypes.c_double,ctypes.c_double,ptr,ctypes.c_int,ptr]
        lib.protection_reason.argtypes=[ctypes.c_void_p];lib.protection_reason.restype=ctypes.c_char_p
        cases=[dict(name='unpowered',baseline=True),dict(name='exit_20ms',exit_at=.02),dict(name='command_timeout'),dict(name='feedback_10ms',feedback_delay=.01),dict(name='forward_03',vx=.3),dict(name='backward_02',vx=-.2),dict(name='lateral_02',vy=.2),dict(name='low_friction',friction=.4),dict(name='state_loss',lost_state=.3),dict(name='intentional_stop',cancel_at=.02)]
        if a.cases:cases=json.loads(a.cases.read_text())
        results=[]
        for c in cases:
            r=run(lib,c,a.output/c['name']);results.append(r);print(json.dumps(r),flush=True)
        a.output.mkdir(parents=True,exist_ok=True)
        files=['onboardmimic/robots/unitree_g1/native_bridge/protection_roll.hpp','onboardmimic/robots/unitree_g1/native_bridge/roll_parameters.hpp','tests/simulation/protection_roll_shim.cpp']
        (a.output/'summary.json').write_text(json.dumps(dict(results=results,mujoco=mujoco.__version__,source_sha256={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},hardware_ready=False),indent=2))
if __name__=='__main__':main()
