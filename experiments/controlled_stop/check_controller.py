"""Exact experiment controller with simulated support during preparation."""
import argparse,json,time
from pathlib import Path
import numpy as np,mujoco
from controller import Controller,BASE
from native_model import NativeModel
p=argparse.ArgumentParser();p.add_argument('xml');p.add_argument('output');p.add_argument('--checkpoint');p.add_argument('--recovery',action='store_true');p.add_argument('--constrained-support',action='store_true');p.add_argument('--extended',action='store_true');p.add_argument('--trace-dir',type=Path);p.add_argument('--yaw-deg',type=float,default=0);p.add_argument('--legacy-world-feedback',action='store_true');p.add_argument('--case',choices=['nominal','push_forward','compensation_sustained','combined','support_failure']);p.add_argument('--stage',choices=['handoff_hold','shallow','full'],default='full');p.add_argument('--stage-trace',type=Path);p.add_argument('--effort-profile',type=Path);p.add_argument('--handoff-seconds',type=float,default=.5);a=p.parse_args()
root=Path(__file__).resolve().parent;model=NativeModel(root/'model.json');trajectory=np.load(root/'trajectory.npz')
if a.effort_profile:
 profile=json.loads(a.effort_profile.read_text())
 assert [x['joint'] for x in profile['joints']]==model.spec['joint_names']
 model.spec['simulation_effort_limits_nm']=[min(x['urdf_effort_nm'],x['simulation_effort_nm']) for x in profile['joints']]
results=[]
cases=('nominal','feedback_delay_20ms','browser_lost','compensation_spike','compensation_sustained','compensation_ramp') if a.recovery else ('nominal','feedback_delay_20ms','browser_lost')
if a.extended:cases=('mass_plus_10pct','mass_minus_10pct','friction_0.4','delay_40ms','joint_noise','imu_pitch_bias','push_forward','push_backward','combined')
if a.case:cases=(a.case,)
if a.trace_dir:a.trace_dir.mkdir(parents=True,exist_ok=True)
if a.stage!='full':cases=('nominal','feedback_delay_20ms')
base_model=model
class InjectedModel:
 spec=base_model.spec
 time=0.
 def calculate(self,*args):
  result=base_model.calculate(*args)
  if (case=='compensation_spike' and 1.5<=self.time<2.1) or (case in ('compensation_sustained','combined') and self.time>=1.5):
   result['feedforward']=result['feedforward'].copy();result['feedforward'][4]+=80
  if case=='support_failure' and self.time>=1.5:result['feet'][0,2]+=.1
  if case=='compensation_ramp' and self.time>=1:
   result['feedforward']=result['feedforward'].copy();result['feedforward'][4]+=min(60,20*(self.time-1))
  return result
for case in cases:
 model=InjectedModel()
 m=mujoco.MjModel.from_xml_path(a.xml);m.opt.timestep=.002
 if case in ('mass_plus_10pct','mass_minus_10pct','combined'):
  factor=.9 if case=='mass_minus_10pct' else 1.1;m.body_mass[:]*=factor;m.body_inertia[:]*=factor
 if case in ('friction_0.4','combined'):m.geom_friction[:,0]=.4
 d=mujoco.MjData(m);mujoco.mj_setConst(m,d)
 js=[m.joint(n+'_joint').id for n in model.spec['joint_names']];qa=m.jnt_qposadr[js];va=m.jnt_dofadr[js];act=[list(m.actuator_trnid[:,0]).index(j) for j in js]
 if a.effort_profile:
  limits=np.asarray(model.spec['simulation_effort_limits_nm']);m.actuator_ctrlrange[act,0]=-limits;m.actuator_ctrlrange[act,1]=limits
 d.qpos[:7]=[0,0,.756,1,0,0,0];d.qpos[qa]=BASE;mujoco.mj_forward(m,d)
 # Keep both sole sphere contacts on the plane.
 feet=model.spec['feet'];geoms=[g for g in range(m.ngeom) if m.geom_bodyid[g] in feet and m.geom_type[g]==mujoco.mjtGeom.mjGEOM_SPHERE]
 d.qpos[2]-=min(d.geom_xpos[g,2]-m.geom_size[g,0] for g in geoms)+.0005
 initial=d.qpos[:7].copy()
 if a.checkpoint:
  checkpoint=np.load(a.checkpoint);d.qpos[:]=checkpoint['qpos'];d.qvel[:]=checkpoint['qvel'];d.qacc_warmstart[:]=checkpoint['qacc_warmstart']
  from model import rotation
  yaw=np.radians(a.yaw_deg);quat=np.array([np.cos(yaw/2),0,0,np.sin(yaw/2)]);out=np.zeros(4);mujoco.mju_mulQuat(out,quat,d.qpos[3:7]);d.qpos[3:7]=out
  d.qpos[:3]=rotation(quat)@d.qpos[:3];d.qvel[:3]=rotation(quat)@d.qvel[:3];d.qacc_warmstart[:3]=rotation(quat)@d.qacc_warmstart[:3]
  c=Controller.handoff(model,trajectory,d.qpos[qa],d.qvel[va],checkpoint['command'],0,recovery_enabled=a.recovery,trial_stage=a.stage,constrained_support=a.constrained_support)
 else:c=Controller(model,trajectory,BASE,0)
 stages=[];stage_index=0;stage_holds=[];stage_since=None
 if a.stage_trace:
  from stages import calibrated_stages
  stages=calibrated_stages(np.loadtxt(a.stage_trace,delimiter=','));c.motion_limit=stages[0]['progress_s']
 c.handoff_seconds=a.handoff_seconds
 c.heading_frame=not a.legacy_world_feedback
 cmd=None;history=[];error=None;max_speed=0.;min_z=10.;max_step=0.;prev=None;max_total_ratio=0.;limited_at=None;timing=[];trace=[];snapshots=[];rng=np.random.default_rng(19);nonfoot=False;max_tilt=0.
 for step in range(10000):
  t=step*.002;model.time=t;mujoco.mj_forward(m,d);history.append((d.qpos.copy(),d.qvel.copy()))
  if step%2==0:
   delay=20 if case=='delay_40ms' else (10 if case in ('feedback_delay_20ms','combined') else 0)
   original=history[max(0,step-delay)];obs=[original[0].copy(),original[1].copy()]
   if case in ('joint_noise','combined'):
    obs[0][qa]+=rng.normal(0,np.radians(.1),29);obs[1][va]+=rng.normal(0,.02,29)
   if case in ('imu_pitch_bias','combined'):
    angle=np.radians(1);out=np.zeros(4);mujoco.mju_mulQuat(out,obs[0][3:7],np.array([np.cos(angle/2),0,np.sin(angle/2),0]));obs[0][3:7]=out
   try:
    if not a.checkpoint and step==4000:c.lower(t,obs[0][qa],obs[1][va])
    begin=time.perf_counter();cmd=c.tick(t,obs[0][qa],obs[1][va],obs[0][3:7],obs[1][3:6],heartbeat=not(case=='browser_lost' and t>(2 if a.checkpoint else 10)))
    timing.append((time.perf_counter()-begin)*1000)
   except ValueError as e:error=str(e);break
   if stages and c.phase=='holding':
    if stage_since is None:stage_since=t
    if t-stage_since>=2 and stage_index<len(stages)-1:
     if abs(d.qpos[2]-stages[stage_index]['height_m'])>.02 or max(abs(d.qvel[va]))>.2:
      error='stage did not settle within height/speed bounds';break
     stage_holds.append(dict(index=stage_index,time=t,height=float(d.qpos[2]),max_speed=float(max(abs(d.qvel[va])))))
     stage_index+=1;c.motion_limit=stages[stage_index]['progress_s'];c.phase='lowering';stage_since=None
   if prev is not None:max_step=max(max_step,float(max(abs(cmd[:,0]-prev[:,0]))))
   prev=cmd.copy()
   if c.phase=='limited_hold' and limited_at is None:limited_at=t
   tau=cmd[:,2]+cmd[:,3]*(cmd[:,0]-obs[0][qa])+cmd[:,4]*(cmd[:,1]-obs[1][va])
   max_total_ratio=max(max_total_ratio,float(max(abs(tau)/np.asarray(model.spec['simulation_effort_limits_nm']))))
  d.xfrc_applied[:]=0
  if case in ('push_forward','push_backward','combined') and 2<=t<2.2:d.xfrc_applied[m.body('torso_link').id,0]=-10 if case=='push_backward' else 10
  applied=cmd
  d.ctrl[act]=np.clip(applied[:,2]+applied[:,3]*(applied[:,0]-d.qpos[qa])+applied[:,4]*(applied[:,1]-d.qvel[va]),m.actuator_ctrlrange[act,0],m.actuator_ctrlrange[act,1])
  for con in d.contact:
   bodies={int(m.geom_bodyid[con.geom1]),int(m.geom_bodyid[con.geom2])}
   if 0 in bodies and not any(f in bodies for f in feet):nonfoot=True
  torso=m.body('torso_link').id;tilt=float(np.degrees(np.arccos(np.clip(d.xmat[torso].reshape(3,3)[2,2],-1,1))));max_tilt=max(max_tilt,tilt)
  if a.trace_dir and step%10==0:snapshots.append(d.qpos.copy());trace.append([t,float(d.qpos[2]),tilt,c.motion_time,float(max(abs(d.qvel[va]))),float(max(abs(cmd[:,2])))])
  mujoco.mj_step(m,d)
  if not a.checkpoint and t<6:d.qpos[:7]=initial;d.qvel[:6]=0 # Supported initial stiffness ramp only.
  min_z=min(min_z,float(d.qpos[2]));max_speed=max(max_speed,float(max(abs(d.qvel[va]))))
  if min_z<.25:error='fell';break
 result=dict(stop_time_s=t,case=case,yaw_deg=a.yaw_deg,heading_frame=c.heading_frame,error=error,phase=c.phase,final_height=float(d.qpos[2]),max_speed=max_speed,max_target_step=max_step,limited_at=limited_at,max_total_ratio=max_total_ratio,nonfoot_contact=nonfoot,max_torso_tilt_deg=max_tilt,compute_p99_ms=float(np.percentile(timing,99)) if timing else None,compute_max_ms=max(timing) if timing else None)
 expected='paused' if case=='browser_lost' else 'holding'
 if case=='support_failure':expected='support_hold'
 result['pass']=error is None and not nonfoot and max_tilt<35 and c.phase==expected and (expected!='holding' or (abs(d.qpos[2]-.35)<.05 if a.stage=='full' else d.qpos[2]>.65)) and (not a.recovery or max_total_ratio<=1.000001)
 if a.trace_dir:
  if error:
   mujoco.mj_forward(m,d)
   terminal_tilt=float(np.degrees(np.arccos(np.clip(d.xmat[m.body('torso_link').id].reshape(3,3)[2,2],-1,1))))
   snapshots.append(d.qpos.copy());trace.append([t,float(d.qpos[2]),terminal_tilt,c.motion_time,float(max(abs(d.qvel[va]))),float(max(abs(cmd[:,2]))) if cmd is not None else 0.])
  np.save(a.trace_dir/(case+'-qpos.npy'),snapshots);np.savetxt(a.trace_dir/(case+'.csv'),trace,delimiter=',',header='time,pelvis_height,torso_tilt,progress,max_joint_speed,max_feedforward')
 if stages:
  result['stage_holds']=stage_holds;result['stages']=stages
  result['pass']=error is None and not nonfoot and max_tilt<35 and c.phase=='holding' and stage_index==len(stages)-1 and abs(d.qpos[2]-stages[-1]['height_m'])<.02 and max(abs(d.qvel[va]))<.2 and max_total_ratio<=1.000001
 result['pass']=bool(result['pass']);results.append(result);print(json.dumps(result),flush=True)
Path(a.output).write_text(json.dumps(results,indent=2)+'\n');assert all(r['pass'] for r in results)
