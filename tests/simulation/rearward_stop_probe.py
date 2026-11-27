"""Offline G1 rearward-lowering study. No hardware/ROS/SONIC interfaces.
Feedback uses privileged simulator COM/velocity and model gravity; not a deployable
robot controller. All variants share initialization, gains, model and torque limits.
"""
import argparse,json,hashlib
from pathlib import Path
import numpy as np
import mujoco
from scipy.optimize import least_squares
ROOT=Path(__file__).resolve().parents[2]
def smooth(x):x=np.clip(x,0,1);return x*x*(3-2*x)
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--model',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--duration',type=float,default=4.);p.add_argument('--release',choices=['hold','timed'],default='hold');p.add_argument('--drop',type=float,default=.20);p.add_argument('--back',type=float,default=.08);p.add_argument('--hold',type=float,default=2.);p.add_argument('--ankle-min',type=float,default=-.85);p.add_argument('--standing-only',action='store_true');a=p.parse_args()
 if not -.85 <= a.ankle_min <= -.6:p.error('ankle minimum must be -0.85..-0.6')
 if not .1 <= a.drop <= .45 or not 0 <= a.back <= .2 or not 1 <= a.hold <= 30:p.error('invalid trajectory bounds')
 if not 2 <= a.duration <= 8:p.error('duration must be 2..8 seconds')
 a.output.mkdir(parents=True,exist_ok=False)
 m=mujoco.MjModel.from_xml_path(str(a.model));m.opt.timestep=.002
 names=[s+'_'+n for s in ('left','right') for n in ('hip_pitch','hip_roll','hip_yaw','knee','ankle_pitch','ankle_roll')]+['waist_yaw','waist_roll','waist_pitch']+[s+'_'+n for s in ('left','right') for n in ('shoulder_pitch','shoulder_roll','shoulder_yaw','elbow','wrist_roll','wrist_pitch','wrist_yaw')]
 joints=[m.joint(n+'_joint').id for n in names];qa=m.jnt_qposadr[joints];va=m.jnt_dofadr[joints];act=np.array([list(m.actuator_trnid[:,0]).index(j) for j in joints])
 feet=[m.body(s+'_ankle_roll_link').id for s in ('left','right')];torso=m.body('torso_link').id
 q0=np.array(json.loads((ROOT/'onboardmimic/backends/sonic/deploy/onboard/baseline_q.json').read_text()))
 d=mujoco.MjData(m);d.qpos[:7]=[0,0,.78874,1,0,0,0];d.qpos[qa]=q0;mujoco.mj_forward(m,d)
 footgeoms=[g for g in range(m.ngeom) if m.geom_bodyid[g] in feet and m.geom_type[g]==mujoco.mjtGeom.mjGEOM_SPHERE]
 d.qpos[2]-=min(d.geom_xpos[g,2]-m.geom_size[g,0] for g in footgeoms);d.qpos[2]-=.0005;mujoco.mj_forward(m,d)
 initial=d.qpos.copy();z0=d.qpos[2];foot0=d.xpos[feet[0]].copy();com0=d.subtree_com[1].copy()
 ik=mujoco.MjData(m)
 def assign(v):
  ik.qpos[:]=initial;ik.qpos[0]=v[0];ik.qpos[2]=v[1];ik.qpos[3:7]=[np.cos(v[2]/2),0,np.sin(v[2]/2),0]
  for o in [0,6]:
   ik.qpos[qa[o]]=v[3];ik.qpos[qa[o+3]]=v[4];ik.qpos[qa[o+4]]=v[5]
  ik.qpos[qa[14]]=v[6];mujoco.mj_forward(m,ik)
 v=np.array([0,z0,0,q0[0],q0[3],q0[4],0.])
 poses=[];errors=[]
 for t in np.linspace(0,a.duration,61):
  w=smooth(t/a.duration)
  def residual(v):
   assign(v);footpitch=np.arctan2(ik.xmat[feet[0]].reshape(3,3)[0,2],ik.xmat[feet[0]].reshape(3,3)[2,2]);tp=np.arctan2(ik.xmat[torso].reshape(3,3)[0,2],ik.xmat[torso].reshape(3,3)[2,2])
   return np.array([(ik.xpos[feet[0],0]-foot0[0])*10,(ik.xpos[feet[0],2]-foot0[2])*10,footpitch,
    (v[0]+a.back*w)*10,(v[1]-(z0-a.drop*w))*10,(ik.subtree_com[1,0]-com0[0])*10,tp-.25*w])
  fit=least_squares(residual,v,bounds=([-0.3,.25,-.5,-2.3,.1,a.ankle_min,-.5],[.2,.85,.5,.4,2.5,.5,.5]),max_nfev=100)
  v=fit.x;assign(v);poses.append(ik.qpos[qa].copy());errors.append(float(np.linalg.norm(fit.fun)))
 poses=np.array(poses);np.savez(a.output/'trajectory.npz',q=poses,time=np.linspace(0,a.duration,61),ik_error=errors)
 arm=np.array([.025101925,.025101925,.010177520,.025101925,2*.003609725,2*.003609725]*2+[.010177520,2*.003609725,2*.003609725]+[.003609725]*5+[.00425]*2+[.003609725]*5+[.00425]*2)
 kp=arm*(20*np.pi)**2;kd=4*arm*20*np.pi
 rows=[]
 for case,vx,vy in ([('standing',0,0)] if a.standing_only else [('standing',0,0),('forward_0.3',.3,0),('sideways_0.2',0,.2)]):
  for variant in (['rearward_contact_feedback'] if a.standing_only else ['damping','simple_squat','rearward','rearward_feedback','rearward_contact_feedback']):
   d=mujoco.MjData(m);d.qpos[:]=initial;d.qvel[0]=vx;d.qvel[1]=vy;mujoco.mj_forward(m,d)
   lastcom=d.subtree_com[1].copy();trace=[];snap=[];fall=None;impact=0.;release=None;limit_margin=100.;nonfoot_contact=False
   for step in range(int((a.duration+a.hold)/.002)):
    t=step*.002;com=d.subtree_com[1].copy();cv=(com-lastcom)/.002;lastcom=com
    if variant=='simple_squat':
     target=q0.copy()
     for o in [0,6]:target[o]=-.9;target[o+3]=1.7;target[o+4]=-.8
     target=q0+(target-q0)*smooth(t/a.duration)
    else:
     idx=min(t/a.duration*60,60);lo=int(idx);hi=min(lo+1,60);target=poses[lo]*(1-(idx-lo))+poses[hi]*(idx-lo)
    target=target.copy()
    if variant in ('rearward_feedback','rearward_contact_feedback'):
     correction=np.clip(1.5*(com[0]-com0[0])+.3*cv[0],-.2,.2)
     lateral=np.clip(-1.5*(com[1]-com0[1])-.3*cv[1],-.15,.15)
     for o in [0,6]:target[o+4]+=correction;target[o+5]+=lateral
    target=np.clip(target,m.jnt_range[joints,0]+.01,m.jnt_range[joints,1]-.01)
    if a.ankle_min>-.85:
     for ankle in [4,10]:target[ankle]=max(target[ankle],a.ankle_min)
    gain=1-smooth((t-a.duration)/.5) if a.release=='timed' and t>=a.duration else 1
    # Deliberately examine timed release too, not a proposed production gate.
    if a.release=='timed' and t>=a.duration and release is None:release=t
    gravity=d.qfrc_bias[va] if variant=='rearward_feedback' else np.zeros(29)
    if variant=='rearward_contact_feedback':
     jac=[]
     for body in feet:
      jp=np.zeros((3,m.nv));jr=np.zeros((3,m.nv));mujoco.mj_jacBody(m,d,jp,jr,body);jac.extend([jp,jr])
     jac=np.vstack(jac)
     forces=np.linalg.lstsq(jac[:,:6].T,d.qfrc_bias[:6],rcond=None)[0]
     gravity=d.qfrc_bias[va]-jac[:,va].T@forces
    torque=(-8*d.qvel[va] if variant=='damping' else gain*(kp*(target-d.qpos[qa])-kd*d.qvel[va]+gravity))
    d.ctrl[act]=np.clip(torque,m.actuator_ctrlrange[act,0],m.actuator_ctrlrange[act,1])
    w,x,y,z=d.xquat[torso];tilt=float(np.arccos(np.clip(1-2*(x*x+y*y),-1,1)));pitch=float(np.arctan2(d.xmat[torso].reshape(3,3)[0,2],d.xmat[torso].reshape(3,3)[2,2]))
    if fall is None and (tilt>np.pi/4 or d.qpos[2]<.25):fall=t
    limit_margin=min(limit_margin,float(np.min(np.minimum(d.qpos[qa]-m.jnt_range[joints,0],m.jnt_range[joints,1]-d.qpos[qa]))))
    force=0
    for contact in range(d.ncon):
     con=d.contact[contact];bodies={int(m.geom_bodyid[con.geom1]),int(m.geom_bodyid[con.geom2])}
     if 0 in bodies and not any(f in bodies for f in feet):nonfoot_contact=True
     cf=np.zeros(6);mujoco.mj_contactForce(m,d,contact,cf);force+=max(0,cf[0])
    impact=max(impact,force)
    trace.append([t,d.qpos[0],d.qpos[2],np.degrees(pitch),np.degrees(tilt),force,max(abs(d.qvel[va])),gain])
    if step%10==0:snap.append(d.qpos.copy())
    mujoco.mj_step(m,d)
    if not np.isfinite(d.qpos).all():raise RuntimeError('Nonfinite physics')
   name=case+'-'+variant
   np.savetxt(a.output/(name+'.csv'),trace,delimiter=',',header='time,base_x,base_z,pitch_deg,tilt_deg,total_contact_normal_N,max_joint_speed,gain',comments='')
   np.save(a.output/(name+'-qpos.npy'),snap)
   rows.append(dict(case=case,variant=variant,fall_seconds=fall,tilt_at_end_deg=trace[int(a.duration/.002)][4],base_height_at_end=trace[int(a.duration/.002)][2],peak_contact_normal_N=impact,max_tilt_deg=max(r[4] for r in trace),max_joint_speed=max(r[6] for r in trace),final_base_height=trace[-1][2],final_tilt_deg=trace[-1][4],joint_limit_margin_rad=limit_margin,nonfoot_ground_contact=nonfoot_contact))
 report=dict(scope='Privileged-state MuJoCo comparison; no SONIC or hardware; optional timed release is an adverse test, not a safety claim',model=str(a.model),duration=a.duration,ankle_min=a.ankle_min,drop=a.drop,back=a.back,hold=a.hold,fall_criterion='tilt >45 degrees or base z <0.25m',release=a.release,ik_max_residual=max(errors),cases=rows,hardware_ready=False)
 (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report,indent=2))
if __name__=='__main__':main()
