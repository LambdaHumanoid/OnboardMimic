"""Simulation-only robustness study of the selected 0.35 m candidate.
Uses privileged base position, COM and support wrench estimation. No hardware
interface. Synthetic supporting command is not an actual SONIC handoff.
"""
import argparse,hashlib,json
from pathlib import Path
import mujoco
import numpy as np

def smooth(x):
 x=np.clip(x,0,1);return x*x*(3-2*x)

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--model',type=Path,required=True);p.add_argument('--trajectory',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 p.add_argument('--checkpoint',type=Path,help='SONIC standing checkpoint from isolated sonic_rollout')
 p.add_argument('--slew-limits',action='store_true',help='Limit target slew to 1.5 rad/s and feedforward slew to 100 Nm/s')
 p.add_argument('--portable-model',action='store_true',help='Use standalone rigid-body computation intended for the robot')
 p.add_argument('--sensor-only',action='store_true',help='Reconstruct root translation from fixed foot anchors, joint angles and IMU')
 a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 portable=None
 if a.portable_model:
  import sys
  module=Path(__file__).resolve().parents[2]/'experiments/controlled_stop';sys.path.insert(0,str(module))
  from native_model import NativeModel
  portable=NativeModel(module/'model.json')
 checkpoint=np.load(a.checkpoint) if a.checkpoint else None
 nominal=mujoco.MjModel.from_xml_path(str(a.model));nominal.opt.timestep=.002
 names=[s+'_'+n for s in ('left','right') for n in ('hip_pitch','hip_roll','hip_yaw','knee','ankle_pitch','ankle_roll')]+['waist_yaw','waist_roll','waist_pitch']+[s+'_'+n for s in ('left','right') for n in ('shoulder_pitch','shoulder_roll','shoulder_yaw','elbow','wrist_roll','wrist_pitch','wrist_yaw')]
 js=[nominal.joint(n+'_joint').id for n in names];qa=nominal.jnt_qposadr[js];va=nominal.jnt_dofadr[js]
 act=np.array([list(nominal.actuator_trnid[:,0]).index(j) for j in js])
 feet=[nominal.body(s+'_ankle_roll_link').id for s in ('left','right')];torso=nominal.body('torso_link').id
 trajectory=np.load(a.trajectory);poses=trajectory['q'];times=trajectory['time'];duration=float(times[-1])
 q0=np.array([-.312,0,0,.669,-.363,0]*2+[0,0,0]+[.2,.2,0,.6,0,0,0]+[.2,-.2,0,.6,0,0,0])
 arm=np.array([.025101925,.025101925,.010177520,.025101925,2*.003609725,2*.003609725]*2+[.010177520,2*.003609725,2*.003609725]+[.003609725]*5+[.00425]*2+[.003609725]*5+[.00425]*2)
 kp=arm*(20*np.pi)**2;kd=4*arm*20*np.pi
 def gravity(data):
  if portable is not None:return portable.calculate(data.qpos[qa],data.qvel[va],data.qpos[3:7],data.qvel[3:6])['feedforward']
  jac=[]
  for body in feet:
   jp=np.zeros((3,nominal.nv));jr=np.zeros((3,nominal.nv));mujoco.mj_jacBody(nominal,data,jp,jr,body);jac.extend([jp,jr])
  jac=np.vstack(jac);f=np.linalg.lstsq(jac[:,:6].T,data.qfrc_bias[:6],rcond=None)[0]
  return data.qfrc_bias[va]-jac[:,va].T@f
 scenarios=[dict(name='baseline_unblended',blend=0),dict(name='handoff_0.5s',blend=.5),
  dict(name='delay_20ms',delay=.02),dict(name='delay_40ms',delay=.04),
  dict(name='ankle_bias_plus_1deg',bias=1.),dict(name='ankle_bias_minus_1deg',bias=-1.),
  dict(name='imu_pitch_plus_1deg',imu=1.),dict(name='imu_pitch_minus_1deg',imu=-1.),
  dict(name='joint_noise_0.1deg',noise=.1),dict(name='mass_plus_5pct',mass=1.05),
  dict(name='mass_minus_5pct',mass=.95),dict(name='friction_0.4',friction=.4),
  dict(name='push_forward_lowering',push=(5.,0.,2.)),dict(name='push_backward_lowering',push=(-5.,0.,2.)),
  dict(name='push_sideways_lowering',push=(0.,5.,2.)),
  dict(name='push_forward_hold',push=(5.,0.,8.)),dict(name='push_sideways_hold',push=(0.,5.,8.)),
  dict(name='combined',delay=.02,noise=.1,bias=.5,imu=.5,mass=1.05)]
 if checkpoint is not None:
  scenarios=[dict(name='sonic_handoff_immediate',blend=.5),dict(name='sonic_handoff_watchdog_100ms',blend=.5,start_delay=.1),dict(name='sonic_handoff_noisy',blend=.5,start_delay=.1,delay=.02,noise=.1,imu=.5)]
 results=[]
 for case in scenarios:
  model=mujoco.MjModel.from_xml_path(str(a.model));model.opt.timestep=.002
  if 'mass' in case:model.body_mass[:]*=case['mass'];model.body_inertia[:]*=case['mass']
  if 'friction' in case:model.geom_friction[:,0]=case['friction']
  d=mujoco.MjData(model);mujoco.mj_setConst(model,d)
  d.qpos[:7]=[0,0,.78874,1,0,0,0];d.qpos[qa]=q0;mujoco.mj_forward(model,d)
  geoms=[g for g in range(model.ngeom) if model.geom_bodyid[g] in feet and model.geom_type[g]==mujoco.mjtGeom.mjGEOM_SPHERE]
  if checkpoint is None:
   d.qpos[2]-=min(d.geom_xpos[g,2]-model.geom_size[g,0] for g in geoms)+.0005
  else:
   assert checkpoint['qpos'].shape==d.qpos.shape
   d.qpos[:]=checkpoint['qpos'];d.qvel[:]=checkpoint['qvel']
   d.qacc_warmstart[:]=checkpoint['qacc_warmstart']
  mujoco.mj_forward(model,d)
  est=mujoco.MjData(nominal);est.qpos[:]=d.qpos;est.qvel[:]=0;mujoco.mj_forward(nominal,est)
  anchors=est.xpos[feet].mean(axis=0).copy();last_est_position=est.qpos[:3].copy();max_position_error=0.
  com0=est.subtree_com[1].copy();last_com=com0.copy();filtered_v=np.zeros(3)
  held_q=q0+gravity(est)/kp
  held_dq=np.zeros(29);held_ff=np.zeros(29);held_kp=kp.copy();held_kd=kd.copy()
  if checkpoint is not None:
   held_q,held_dq,held_ff,held_kp,held_kd=checkpoint['command'].T.copy()
  history=[];rng=np.random.default_rng(20261116);trace=[];snap=[];fall=None;contact_bad=False
  aborted_trial=None
  min_margin=100.;max_q_step=0.;max_ff_step=0.;last_command=None;first_command_error=None;max_speed=0.
  blend=case.get('blend',.5)
  for step in range(int((duration+10)/.002)):
   t=step*.002;motion_time=max(0,t-case.get('start_delay',0));history.append((d.qpos.copy(),d.qvel.copy()))
   observed=history[max(0,step-round(case.get('delay',0)/.002))]
   est.qpos[:]=observed[0];est.qvel[:]=observed[1]
   est.qpos[qa]+=rng.normal(0,np.radians(case.get('noise',0)),29)
   est.qpos[qa[[4,10]]]+=np.radians(case.get('bias',0))
   if 'imu' in case:
    angle=np.radians(case['imu']);quat=np.array([np.cos(angle/2),0,np.sin(angle/2),0]);out=np.zeros(4);mujoco.mju_mulQuat(out,est.qpos[3:7],quat);est.qpos[3:7]=out
   if a.sensor_only:
    # No simulator root position/linear velocity enters the estimator.
    # Local foot anchors come from nominal initial geometry; this assumes
    # both feet stay fixed and is not an independently verified contact sensor.
    est.qpos[:3]=0;est.qvel[:3]=0;mujoco.mj_forward(nominal,est)
    foot_positions=est.xpos[feet] if portable is None else portable.calculate(est.qpos[qa],est.qvel[va],est.qpos[3:7],est.qvel[3:6])['feet']
    est.qpos[:3]=anchors-foot_positions.mean(axis=0)
    est.qvel[:3]=(est.qpos[:3]-last_est_position)/.002
    last_est_position=est.qpos[:3].copy()
   mujoco.mj_forward(nominal,est)
   max_position_error=max(max_position_error,float(np.linalg.norm(est.qpos[:3]-d.qpos[:3])))
   com=est.subtree_com[1].copy() if portable is None else est.qpos[:3]+portable.calculate(est.qpos[qa],est.qvel[va],est.qpos[3:7],est.qvel[3:6])['com']
   velocity=(com-last_com)/.002;last_com=com
   # Baseline reproduces the preceding ideal probe; noise cases use a 20ms filter.
   if case.get('noise',0):filtered_v+=.002/(.02+.002)*(velocity-filtered_v);velocity=filtered_v.copy()
   idx=min(motion_time/duration*(len(poses)-1),len(poses)-1);lo=int(idx);hi=min(lo+1,len(poses)-1)
   target=poses[lo]*(1-(idx-lo))+poses[hi]*(idx-lo)
   for offset in [0,6]:
    target[offset+4]+=np.clip(1.5*(com[0]-com0[0])+.3*velocity[0],-.2,.2)
    target[offset+5]+=np.clip(-1.5*(com[1]-com0[1])-.3*velocity[1],-.15,.15)
   target=np.clip(target,model.jnt_range[js,0]+.01,model.jnt_range[js,1]-.01)
   target[[4,10]]=np.maximum(target[[4,10]],-.8)
   weight=smooth(motion_time/blend) if blend else 1.
   command=(1-weight)*held_q+weight*target;ff=(1-weight)*held_ff+weight*gravity(est)
   command_kp=(1-weight)*held_kp+weight*kp;command_kd=(1-weight)*held_kd+weight*kd;command_dq=(1-weight)*held_dq
   if a.slew_limits and last_command is not None:
    command=np.clip(command,last_command[0]-.003,last_command[0]+.003)
    ff=np.clip(ff,last_command[1]-.2,last_command[1]+.2)
   if first_command_error is None:first_command_error=float(np.max(abs(command-held_q)))
   if last_command is not None:
    max_q_step=max(max_q_step,float(np.max(abs(command-last_command[0]))))
    max_ff_step=max(max_ff_step,float(np.max(abs(ff-last_command[1]))))
   last_command=(command.copy(),ff.copy())
   # Motor PD uses current joint feedback, as the hardware motor loop does.
   # Delay/noise affect high-level estimation, not the embedded motor servo.
   tau=ff+command_kp*(command-d.qpos[qa])+command_kd*(command_dq-d.qvel[va])
   d.ctrl[act]=np.clip(tau,model.actuator_ctrlrange[act,0],model.actuator_ctrlrange[act,1])
   d.xfrc_applied[:]=0
   if 'push' in case:
    fx,fy,at=case['push']
    if at<=t<at+.2:d.xfrc_applied[torso,:2]=[fx,fy]
   w,x,y,z=d.xquat[torso];tilt=float(np.degrees(np.arccos(np.clip(1-2*(x*x+y*y),-1,1))))
   if fall is None and (tilt>45 or d.qpos[2]<.25):fall=t
   min_margin=min(min_margin,float(np.min(np.minimum(d.qpos[qa]-model.jnt_range[js,0],model.jnt_range[js,1]-d.qpos[qa]))))
   max_speed=max(max_speed,float(np.max(abs(d.qvel[va]))))
   for con in d.contact:
    bodies={int(model.geom_bodyid[con.geom1]),int(model.geom_bodyid[con.geom2])}
    if 0 in bodies and not any(f in bodies for f in feet):contact_bad=True
   trace.append([t,float(d.qpos[2]),tilt,max(abs(d.qvel[va]))])
   if step%10==0:snap.append(d.qpos.copy())
   if tilt>45 or min_margin<-.05 or max_speed>20 or contact_bad:
    aborted_trial=dict(time=t,reason='tilt, joint limit, velocity or non-foot contact threshold')
    break
   mujoco.mj_step(model,d)
   if not np.isfinite(d.qpos).all():raise RuntimeError('nonfinite physics')
  result=dict(scenario=case,max_estimated_position_error_m=max_position_error,aborted_trial=aborted_trial,fall_seconds=fall,nonfoot_ground_contact=contact_bad,
   final_height=trace[-1][1],max_tilt_deg=max(x[2] for x in trace),min_limit_margin_rad=min_margin,
   max_joint_speed=max_speed,first_target_delta_rad=first_command_error,max_target_step_rad=max_q_step,max_ff_step_Nm=max_ff_step)
  result['screen_pass']=aborted_trial is None and fall is None and not contact_bad and min_margin>=0 and abs(result['final_height']-.35)<.05
  if a.slew_limits:result['screen_pass'] &= max_q_step<=.00300001 and max_ff_step<=.20000001
  results.append(result);np.save(a.output/(case['name']+'-qpos.npy'),snap)
  np.savetxt(a.output/(case['name']+'.csv'),trace,delimiter=',',header='time,height,tilt,max_joint_speed')
  print(json.dumps(result),flush=True)
 report=dict(scope=__doc__,portable_model=a.portable_model,checkpoint=str(a.checkpoint) if a.checkpoint else None,sensor_only=a.sensor_only,slew_limits=a.slew_limits,trajectory_sha256=hashlib.sha256(a.trajectory.read_bytes()).hexdigest(),duration=duration,hold=10,results=results,hardware_ready=False)
 (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main()
