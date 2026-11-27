"""Offline coordinated squat candidate from recorded posture; no motor transport.

Feet retain their sampled poses. Pelvis translation/pitch and waist pitch are
solved together with both legs. COM projection is preserved in the model.
This is kinematics and static model evidence, not physical balance validation.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from native_model import NativeModel
from model import rotation
from standing_plan import rotation_error, generate as generate_shallow


def quat_wxyz(r):
    factory=getattr(Rotation,'from_matrix',None) or Rotation.from_dcm
    return np.roll(factory(r).as_quat(),1)


def rotvec_matrix(v):
    r=Rotation.from_rotvec(v)
    return r.as_matrix() if hasattr(r,'as_matrix') else r.as_dcm()


def ry(a):
    return rotvec_matrix([0,a,0])


def torso_rotation(base,q):
    return base @ rotvec_matrix([0,0,q[12]]) @ rotvec_matrix([q[13],0,0]) @ ry(q[14])


def generate(model,q0,quat0,depth=.20,duration=24.,count=161,geometry_weight=1.,relative_pitch_limit=.45):
    q0=np.asarray(q0,dtype=float);quat0=np.asarray(quat0,dtype=float)
    if q0.shape!=(29,) or quat0.shape!=(4,) or not np.isfinite(np.r_[q0,quat0]).all():raise ValueError('Invalid source pose')
    if not 0<depth<=.20 or duration<12 or count<121:raise ValueError('Candidate is limited to 20 cm over at least 12 s')
    limits=np.asarray(model.spec['limits']);R0=rotation(quat0)
    yaw=np.arctan2(R0[1,0],R0[0,0]);heading=rotvec_matrix([0,0,yaw])
    initial=model.calculate(q0,np.zeros(29),quat0,np.zeros(3))
    torso0=heading.T@torso_rotation(R0,q0);pitch0=np.arctan2(torso0[0,2],torso0[2,2])
    # x = both legs, waist pitch, heading-frame pelvis x/y, pelvis pitch delta.
    x=np.r_[q0[:12],q0[14],0.,0.,0.]
    lower=np.r_[limits[:12,0]+.02,limits[14,0]+.02,-.15,-.05,-relative_pitch_limit]
    upper=np.r_[limits[:12,1]-.02,limits[14,1]-.02,.05,.05,relative_pitch_limit]
    # Bound absolute pelvis tilt inside the optimizer, not merely the change
    # from the sampled posture. A later stage may already start inclined.
    amplitude=np.hypot(R0[2,0],R0[2,2]);required=np.cos(np.radians(29.5))
    if amplitude<required:raise ValueError('Source roll leaves no pelvis-tilt margin')
    center=np.arctan2(R0[2,0],R0[2,2]);width=np.arccos(required/amplitude)
    lower[-1]=max(lower[-1],center-width);upper[-1]=min(upper[-1],center+width)
    # Keep room for the existing ankle feedback and -0.8 command floor.
    lower[[4,10]]=np.maximum(lower[[4,10]],-.75)
    if np.any(x<=lower) or np.any(x>=upper):raise ValueError('Source outside candidate margins')
    poses=[q0.copy()];quats=[quat0/np.linalg.norm(quat0)];translations=[np.zeros(3)];rows=[]
    times=np.linspace(0,duration,count)
    for t in times[1:]:
        u=t/duration;w=10*u**3-15*u**4+6*u**5
        drop=depth*w
        def evaluate(v):
            q=q0.copy();q[:12]=v[:12];q[14]=v[12]
            R=R0@ry(v[15]);quat=quat_wxyz(R)
            base=heading@np.array([v[13],v[14],-drop])
            result=model.calculate(q,np.zeros(29),quat,np.zeros(3))
            feet=result['feet']+base
            com=result['com']+base
            torso=heading.T@torso_rotation(R,q)
            return q,quat,base,result,feet,com,np.arctan2(torso[0,2],torso[2,2])
        def residual(v):
            q,quat,base,r,feet,com,pitch=evaluate(v)
            errors=[]
            for i in range(2):errors.extend(geometry_weight*np.r_[10*(feet[i]-initial['feet'][i]),rotation_error(initial['foot_rotations'][i],r['foot_rotations'][i])])
            errors.extend(geometry_weight*10*(com-initial['com'])[:2])
            # Soft regularization selects a smooth economical posture; neither
            # torso pitch nor horizontal pelvis displacement is fixed.
            errors.extend([.01*(pitch-pitch0),.01*v[15],.01*v[13]])
            return np.array(errors)
        fit=least_squares(residual,x,bounds=(lower,upper),max_nfev=100,ftol=1e-10,xtol=1e-10,gtol=1e-10)
        x=fit.x;q,quat,base,r,feet,com,pitch=evaluate(x)
        pe=float(np.max(abs(feet-initial['feet'])))
        re=max(float(np.max(abs(rotation_error(a,b)))) for a,b in zip(initial['foot_rotations'],r['foot_rotations']))
        ce=float(np.max(abs((com-initial['com'])[:2])))
        if not fit.success or pe>1e-4 or re>1e-3 or ce>1e-4:
            raise ValueError('Coordinated IK failed at %.4f m: foot=%g rotation=%g COM=%g'%(drop,pe,re,ce))
        if 1-2*(quat[1]**2+quat[2]**2)<np.cos(np.radians(30)):raise ValueError('Candidate pelvis tilt exceeds 30 degrees')
        poses.append(q);quats.append(quat);translations.append(base)
        rows.append(dict(time_s=float(t),drop_m=float(drop),foot_error_m=pe,foot_rotation_error=re,com_xy_error_m=ce,
            pelvis_pitch_delta_deg=float(np.degrees(x[15])),torso_pitch_deg=float(np.degrees(pitch)),
            max_model_feedforward_nm=float(np.max(abs(r['feedforward'])))))
    poses=np.asarray(poses);speed=float(np.max(abs(np.diff(poses,axis=0)/np.diff(times)[:,None])))
    if speed>.25:raise ValueError('Target speed exceeds 0.25 rad/s')
    plan=dict(q=poses,time=times,quaternion=np.asarray(quats),base_translation=np.asarray(translations),depth_m=np.array(depth))
    report=dict(depth_m=depth,duration_s=duration,max_target_speed_rad_s=speed,
        max_foot_error_m=max(r['foot_error_m'] for r in rows),max_com_xy_error_m=max(r['com_xy_error_m'] for r in rows),
        ankle_pitch_min_rad=poses[:,[4,10]].min(axis=0).tolist(),waist_pitch_end_rad=float(poses[-1,14]),
        final_pelvis_translation_m=translations[-1].tolist(),final_pelvis_pitch_delta_deg=rows[-1]['pelvis_pitch_delta_deg'],
        final_torso_pitch_deg=rows[-1]['torso_pitch_deg'],max_model_feedforward_nm=max(r['max_model_feedforward_nm'] for r in rows),
        arms_unchanged=bool(np.array_equal(poses[:,15:],np.broadcast_to(q0[15:],poses[:,15:].shape))),
        scope='offline_kinematics_not_balance_or_hardware_acceptance',hardware_ready=False,trace=rows)
    return plan,report


def generate_staged(model,q,quat):
    shallow,shallow_report=generate_shallow(model,q,quat)
    deeper,deeper_report=generate(model,shallow['q'][-1],quat,depth=.17)
    shallow_base=np.column_stack((np.zeros(len(shallow['time'])),np.zeros(len(shallow['time'])),
        -.03*(10*(shallow['time']/6)**3-15*(shallow['time']/6)**4+6*(shallow['time']/6)**5)))
    plan=dict(q=np.vstack((shallow['q'],deeper['q'][1:])),
        time=np.r_[shallow['time'],6+deeper['time'][1:]],
        quaternion=np.vstack((np.broadcast_to(np.asarray(quat)/np.linalg.norm(quat),(len(shallow['time']),4)),deeper['quaternion'][1:])),
        base_translation=np.vstack((shallow_base,deeper['base_translation'][1:]+np.array([0,0,-.03]))),
        depth_m=np.array(.20),stage_times=np.array([6.,6+deeper['time'][-1]]),stage_depths_m=np.array([.03,.20]))
    report=dict(scope='offline_kinematics_not_balance_or_hardware_acceptance',hardware_ready=False,
        stage_depths_m=[.03,.20],stage_motion_duration_s=[6.,float(deeper['time'][-1])],
        first_stage=shallow_report,second_stage=deeper_report)
    return plan,report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('sample',type=Path);p.add_argument('output',type=Path);p.add_argument('--from-shallow',action='store_true');a=p.parse_args()
    source=json.loads(a.sample.read_text());f=source['raw_feedback'];q=[m['q'] for m in f['motor_state'][:29]]
    m=NativeModel(Path(__file__).with_name('model.json'))
    plan,report=generate(m,q,f['imu_state']['quaternion'],depth=.17) if a.from_shallow else generate_staged(m,q,f['imu_state']['quaternion'])
    a.output.parent.mkdir(parents=True,exist_ok=True);np.savez(a.output,**plan)
    report['source_wall']=source['wall'];a.output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n')
    summary={k:v for k,v in report.items() if k!='trace'}
    if 'second_stage' in summary:summary['second_stage']={k:v for k,v in summary['second_stage'].items() if k!='trace'}
    print(json.dumps(summary,indent=2))
