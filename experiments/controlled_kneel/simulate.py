"""Offline free-base MuJoCo controlled-kneeling prototype. No robot transport."""
import argparse,json,hashlib,subprocess
from pathlib import Path
import numpy as np
import mujoco
from scipy.optimize import nnls,least_squares

ROOT=Path(__file__).resolve().parents[2]
BUILD=Path('/home/oem/jiangyiming/OnboardMimic_builds/controlled-stop-20261116')
MODEL=Path('/home/oem/jiangyiming/OnboardMimic_builds/jp6-v1.1-20261112/g1/scene_29dof.xml')
CHECKPOINT=BUILD/'sonic-checkpoint/stop-checkpoint.npz'

def smooth(x):
    x=np.clip(x,0,1);return x*x*x*(10-15*x+6*x*x)

def setup():
    m=mujoco.MjModel.from_xml_path(str(MODEL));m.opt.timestep=.002
    names=json.loads((ROOT/'experiments/controlled_stop/model.json').read_text())['joint_names']
    js=[m.joint(n+'_joint').id for n in names];qa=m.jnt_qposadr[js];va=m.jnt_dofadr[js];act=np.array([list(m.actuator_trnid[:,0]).index(j) for j in js])
    limits=np.array([min(x['urdf_effort_nm'],x['simulation_effort_nm']) for x in json.loads((BUILD/'official-effort-comparison.json').read_text())['joints']])
    cp=np.load(CHECKPOINT);d=mujoco.MjData(m);d.qpos[:]=cp['qpos'];d.qvel[:]=cp['qvel'];d.qacc_warmstart[:]=cp['qacc_warmstart'];mujoco.mj_forward(m,d)
    return m,d,qa,va,act,limits,cp,names

def contact_info(m,d):
    forces={};peaks={};points=[];self_pen=0.;head=False
    for i,con in enumerate(d.contact):
        b1=int(m.geom_bodyid[con.geom1]);b2=int(m.geom_bodyid[con.geom2]);v=np.zeros(6);mujoco.mj_contactForce(m,d,i,v)
        if b1!=0 and b2!=0:self_pen=min(self_pen,float(con.dist));continue
        b=b2 if b1==0 else b1;g=con.geom2 if b1==0 else con.geom1
        name=m.body(b).name;force=max(0,float(v[0]));forces[name]=forces.get(name,0)+force;peaks[name]=max(peaks.get(name,0),force)
        mesh=m.geom_dataid[g];meshname=m.mesh(mesh).name if mesh>=0 and m.geom_type[g]==mujoco.mjtGeom.mjGEOM_MESH else ''
        if 'head' in meshname and force>1:head=True
        if con.dist<.003:points.append((b,con.pos.copy()))
    return forces,peaks,points,self_pen,head

def support_bias(m,d,va,points):
    if not points:return np.zeros(29)
    J=[]
    for body,pos in points:
        jp=np.zeros((3,m.nv));jr=np.zeros((3,m.nv));mujoco.mj_jac(m,d,jp,jr,pos,body);J.append(jp[2])
    J=np.asarray(J);A=J[:,:6].T
    try:f=nnls(np.vstack((A,.0001*np.eye(len(J)))),np.r_[d.qfrc_bias[:6],np.zeros(len(J))],maxiter=200)[0]
    except RuntimeError:f=np.maximum(0,np.linalg.lstsq(A,d.qfrc_bias[:6],rcond=None)[0])
    return d.qfrc_bias[va]-J[:,va].T@f

def impact_samples(m,d):
    """Ground contact proxies by region; knee meshes also include the shins."""
    samples={}
    for i,con in enumerate(d.contact):
        b1=int(m.geom_bodyid[con.geom1]);b2=int(m.geom_bodyid[con.geom2])
        if b1 and b2:continue
        body=b2 if b1==0 else b1;geom=con.geom2 if b1==0 else con.geom1
        name=m.body(body).name;mesh=m.geom_dataid[geom]
        meshname=m.mesh(mesh).name if mesh>=0 and m.geom_type[geom]==mujoco.mjtGeom.mjGEOM_MESH else ''
        region='head' if 'head' in meshname else ('knees_shins' if 'knee' in name else ('hands_forearms' if 'wrist' in name or 'elbow' in name else ('feet' if 'ankle' in name else 'other_body')))
        force=np.zeros(6);mujoco.mj_contactForce(m,d,i,force)
        jp=np.zeros((3,m.nv));jr=np.zeros((3,m.nv));mujoco.mj_jac(m,d,jp,jr,con.pos,body)
        row=samples.setdefault(region,dict(force_n=0.,downward_contact_speed_m_s=0.))
        row['force_n']+=max(0.,float(force[0]))
        row['downward_contact_speed_m_s']=max(row['downward_contact_speed_m_s'],max(0.,-float(jp[2]@d.qvel)))
    # Torso BODY also contains head and waist-support geometries. Select the
    # torso shell MESH explicitly; do not infer chest loading from body name.
    for i,con in enumerate(d.contact):
        for geom in (con.geom1,con.geom2):
            mesh=m.geom_dataid[geom]
            if m.geom_type[geom]!=mujoco.mjtGeom.mjGEOM_MESH or mesh<0 or m.mesh(mesh).name!='torso_link':continue
            body=int(m.geom_bodyid[geom]);other=con.geom2 if geom==con.geom1 else con.geom1
            ground=m.geom_bodyid[other]==0
            local=d.xmat[body].reshape(3,3).T@(con.pos-d.xpos[body])
            regions=['torso_shell_ground','torso_front_ground' if local[0]>=0 else 'torso_back_ground'] if ground else ['torso_shell_self']
            force=np.zeros(6);mujoco.mj_contactForce(m,d,i,force)
            jp=np.zeros((3,m.nv));jr=np.zeros((3,m.nv));mujoco.mj_jac(m,d,jp,jr,con.pos,body)
            for region in regions:
                row=samples.setdefault(region,dict(force_n=0.,downward_contact_speed_m_s=0.))
                row['force_n']+=max(0.,float(force[0]))
                if ground:row['downward_contact_speed_m_s']=max(row['downward_contact_speed_m_s'],max(0.,-float(jp[2]@d.qvel)))
            break
    return samples

def pose(q0,hip,knee,ankle,waist,shoulder,elbow):
    q=q0.copy()
    for o in (0,6):q[o:o+6]=[hip,0,0,knee,ankle,0]
    q[12:15]=[0,0,waist]
    q[15:22]=[shoulder,.25,0,elbow,0,0,0];q[22:29]=[shoulder,-.25,0,elbow,0,0,0]
    return q

def hug_pose(m,qa,q):
    """Reach outside each knee. No grasp constraint, adhesion or welded hands."""
    d=mujoco.MjData(m);d.qpos[2]=1.;d.qpos[3]=1.;d.qpos[qa]=q
    result=q.copy();errors={}
    for side,start,sign in [('left',15,1),('right',22,-1)]:
        ids=np.arange(start,start+7);joints=[m.joint(n).id for n in [side+'_'+n+'_joint' for n in ['shoulder_pitch','shoulder_roll','shoulder_yaw','elbow','wrist_roll','wrist_pitch','wrist_yaw']]]
        bounds=m.jnt_range[joints].copy();k=m.body(side+'_knee_link').id;h=m.body(side+'_wrist_yaw_link').id
        if sign>0:bounds[1,0]=.08
        else:bounds[1,1]=-.08
        initial=np.clip(q[ids],bounds[:,0]+.02,bounds[:,1]-.02)
        def residual(x):
            d.qpos[qa[ids]]=x;mujoco.mj_forward(m,d)
            target=d.xpos[k]+d.xmat[k].reshape(3,3)@np.array([.035,sign*.065,.015])
            hand=d.xpos[h]+d.xmat[h].reshape(3,3)@np.array([.025,0,0])
            return np.r_[(hand-target)*10.,.015*(x-initial)]
        fit=least_squares(residual,initial,bounds=(bounds[:,0]+.01,bounds[:,1]-.01),max_nfev=100)
        result[ids]=fit.x;errors[side]=float(np.linalg.norm(residual(fit.x)[:3])/10)
    return result,errors

def simulate(config,out=None):
    m,d,qa,va,act,limits,cp,names=setup();q0=d.qpos[qa].copy()
    delay=config.get('takeover_delay_s',0.);knee_first=config.get('knee_first',False)
    takeover_done=False;knees_stable=0.;arms_release=None
    ranges=m.jnt_range[[m.joint(n+'_joint').id for n in names]]
    if 'friction' in config:
        m.geom_friction[:,0]=config['friction']
    d.qvel[0]+=config.get('initial_vx_m_s',0.)
    mujoco.mj_forward(m,d)
    crouch=pose(q0,config.get('hip1',-1.25),config.get('knee1',2.1),config.get('ankle1',-.75),config.get('waist1',.35),config.get('arm1',-1.),config.get('elbow1',.8))
    kneel=pose(q0,config.get('hip2',-1.9),config.get('knee2',2.7),config.get('ankle2',.35),config.get('waist2',.45),config.get('arm2',-2.),config.get('elbow2',.7))
    fold=pose(q0,config.get('hip3',-2.2),config.get('knee3',2.75),config.get('ankle3',.35),config.get('waist3',.45),config.get('arm3',-2.3),config.get('elbow3',.8))
    hug_errors={}
    if config.get('backward_roll',False):
        crouch,hug_errors['crouch']=hug_pose(m,qa,crouch)
        kneel,hug_errors['sit_back']=hug_pose(m,qa,kneel)
        fold,hug_errors['tuck']=hug_pose(m,qa,fold)
    duration=config.get('duration',18.);times=config.get('times',[0,5.,10.,14.])
    contact_gated=config.get('contact_gated',False)
    kneel_start=None;hand_stable_time=0.;transition_duration=times[3]-times[2]
    kp=np.array([120,100,60,160,50,40]*2+[50,50,60]+[45,35,25,35,10,10,10]*2,dtype=float)*config.get('gain',1.)
    kd=np.array([8,7,4,10,4,3]*2+[4,4,5]+[4,3,2,3,1,1,1]*2,dtype=float)
    initial_com=d.subtree_com[1].copy();last_com=initial_com.copy();com_velocity=np.zeros(3)
    target_prev=q0.copy();ff=np.zeros(29);frames=[];trace=[];events=[];peak_contact={};peak_body_force={};first_contact={};head_impact=False;max_self=0.;max_speed=0.;sat=0;max_tau=np.zeros(29);peak_speed=np.zeros(29)
    impact={};last_torso_velocity=None;peak_torso_acceleration=0.
    for step in range(int(duration/.002)):
        t=step*.002;motion_t=max(0.,t-delay);forces,peaks,points,self_pen,head=contact_info(m,d)
        jp=np.zeros((3,m.nv));jr=np.zeros((3,m.nv));mujoco.mj_jacBodyCom(m,d,jp,jr,m.body('torso_link').id)
        torso_velocity=jp@d.qvel
        if last_torso_velocity is not None:peak_torso_acceleration=max(peak_torso_acceleration,float(np.linalg.norm((torso_velocity-last_torso_velocity)/.002)))
        last_torso_velocity=torso_velocity
        for region,sample in impact_samples(m,d).items():
            row=impact.setdefault(region,dict(first_loaded_contact_s=None,peak_summed_normal_force_n=0.,peak_downward_contact_speed_m_s=0.,first_100ms_normal_impulse_ns=0.))
            row['peak_summed_normal_force_n']=max(row['peak_summed_normal_force_n'],sample['force_n'])
            row['peak_downward_contact_speed_m_s']=max(row['peak_downward_contact_speed_m_s'],sample['downward_contact_speed_m_s'])
            if sample['force_n']>1 and row['first_loaded_contact_s'] is None:row['first_loaded_contact_s']=t
            if row['first_loaded_contact_s'] is not None and t<row['first_loaded_contact_s']+.1-1e-9:row['first_100ms_normal_impulse_ns']+=sample['force_n']*.002
        for name,value in peaks.items():peak_contact[name]=max(peak_contact.get(name,0),value)
        for name,value in forces.items():peak_body_force[name]=max(peak_body_force.get(name,0),value)
        for name,value in forces.items():
            if value>8 and name not in first_contact:first_contact[name]=t;events.append(dict(time=t,event='first_loaded_contact',body=name,force_n=value))
        if head and not head_impact:events.append(dict(time=t,event='head_ground_contact'))
        head_impact|=head;max_self=min(max_self,self_pen)
        if not takeover_done and t>=delay and not config.get('no_takeover',False):
            takeover_done=True
            q0=d.qpos[qa].copy();target_prev=q0.copy()
            events.append(dict(time=t,event='takeover',base_velocity=d.qvel[:6].tolist()))
        bilateral_knees=all(sum(v for n,v in forces.items() if side in n and 'knee' in n)>8 for side in ('left','right'))
        knees_stable=knees_stable+.002 if bilateral_knees else 0.
        if knee_first and arms_release is None and knees_stable>=.04:
            arms_release=t;events.append(dict(time=t,event='knees_loaded_release_arms'))
        bilateral_hands=all(sum(v for n,v in forces.items() if side in n and ('wrist' in n or 'elbow' in n))>8 for side in ('left','right'))
        hand_stable_time=hand_stable_time+.002 if bilateral_hands else 0.
        if kneel_start is None and motion_t>=times[2] and (not contact_gated or hand_stable_time>=.2):
            kneel_start=t;events.append(dict(time=t,event='begin_knee_transfer',bilateral_hands_loaded=bilateral_hands))
        if motion_t<times[1]:stage='crouch';target=q0+(crouch-q0)*smooth(motion_t/times[1])
        elif kneel_start is None:stage='tip_for_knees' if knee_first else 'reach_ground';target=crouch+(kneel-crouch)*smooth((motion_t-times[1])/(times[2]-times[1]))
        elif t<kneel_start+transition_duration:stage='lower_knees';target=kneel+(fold-kneel)*smooth((t-kneel_start)/transition_duration)
        else:stage='hold';target=fold.copy()
        if config.get('backward_roll',False):stage={'reach_ground':'sit_back','lower_knees':'tuck_roll','hold':'hold_tuck'}.get(stage,stage)
        if knee_first:
            tucked=pose(q0,0,0,0,0,config.get('tuck_shoulder',0.),config.get('tuck_elbow',1.8))[15:]
            arm_blend=0. if arms_release is None else smooth((t-arms_release)/.4)
            target[15:]=tucked+(fold[15:]-tucked)*arm_blend
        com=d.subtree_com[1].copy();velocity=(com-last_com)/.002;last_com=com;com_velocity+=.002/(.03+.002)*(velocity-com_velocity)
        if motion_t<config.get('balance_until',5.):
            target[[4,10]]+=np.clip(1.5*(com[0]-initial_com[0])+.3*com_velocity[0],-.15,.15)
            target[[5,11]]+=np.clip(-1.5*(com[1]-initial_com[1])-.3*com_velocity[1],-.12,.12)
        target=np.clip(target,ranges[:,0]+.01,ranges[:,1]-.01)
        rate=config.get('target_rate_rad_s',1.5)*.002
        target=np.clip(target,target_prev-rate,target_prev+rate);dq=(target-target_prev)/.002;target_prev=target.copy()
        if step%5==0:ff=support_bias(m,d,va,points)*config.get('feedforward',1.)
        tau=ff+kp*(target-d.qpos[qa])+kd*(dq-d.qvel[va]);w=smooth(motion_t/config.get('blend_s',.7))
        old=cp['command'];oldtau=old[:,2]+old[:,3]*(old[:,0]-d.qpos[qa])+old[:,4]*(old[:,1]-d.qvel[va])
        blend_from=np.zeros(29) if (delay>0 or 'failure_mode' in config) and config.get('failure_mode','zero_torque')=='zero_torque' else oldtau
        tau=blend_from*(1-w)+tau*w
        if t<delay or config.get('no_takeover',False):
            stage='output_lost';tau=oldtau if config.get('failure_mode','zero_torque')=='last_command_pd' else np.zeros(29)
        sat+=int(np.any(abs(tau)>limits));tau=np.clip(tau,-limits,limits);d.ctrl[act]=tau
        max_tau=np.maximum(max_tau,abs(tau));max_speed=max(max_speed,float(np.max(abs(d.qvel[va]))))
        peak_speed=np.maximum(peak_speed,abs(d.qvel[va]))
        if step%10==0:
            frames.append(d.qpos.copy());trace.append(dict(time=t,phase=stage,base_z=float(d.qpos[2]),torso_pitch_deg=float(np.degrees(np.arctan2(d.xmat[m.body('torso_link').id].reshape(3,3)[0,2],d.xmat[m.body('torso_link').id].reshape(3,3)[2,2]))),max_speed=float(np.max(abs(d.qvel[va]))),forces=forces))
        mujoco.mj_step(m,d)
        if not np.isfinite(d.qpos).all():raise RuntimeError('Nonfinite physics')
    final_forces=contact_info(m,d)[0];final_speed=float(np.max(abs(d.qvel)))
    knees=sum(v for n,v in final_forces.items() if 'knee' in n);hands=sum(v for n,v in final_forces.items() if any(x in n for x in ('wrist','elbow')))
    first_knees=[first_contact.get(side+'_knee_link',float('inf')) for side in ('left','right')]
    first_hands=min([v for n,v in first_contact.items() if 'wrist' in n or 'elbow' in n] or [float('inf')])
    first_upper=min([v for n,v in first_contact.items() if any(x in n for x in ('wrist','elbow','shoulder','torso','pelvis'))] or [float('inf')])
    knee_order=bool(max(first_knees)<first_upper)
    hand_knee_distances={}
    if config.get('backward_roll',False):
        for side,sign in [('left',1),('right',-1)]:
            k=m.body(side+'_knee_link').id;h=m.body(side+'_wrist_yaw_link').id
            target=d.xpos[k]+d.xmat[k].reshape(3,3)@np.array([.035,sign*.065,.015])
            hand=d.xpos[h]+d.xmat[h].reshape(3,3)@np.array([.025,0,0])
            hand_knee_distances[side]=float(np.linalg.norm(hand-target))
    result=dict(config=config,head_impact=head_impact,first_contacts=first_contact,peak_contact_n=peak_contact,max_joint_speed_rad_s=max_speed,
        final_base_z=float(d.qpos[2]),final_speed=final_speed,final_base_linear_speed_m_s=float(np.linalg.norm(d.qvel[:3])),final_base_angular_speed_rad_s=float(np.linalg.norm(d.qvel[3:6])),final_max_joint_speed_rad_s=float(np.max(abs(d.qvel[va]))),final_contacts=final_forces,knee_load_n=knees,hand_forearm_load_n=hands,max_self_penetration_m=-max_self,
        peak_body_normal_force_n=peak_body_force,peak_joint_speed_rad_s=dict(zip(names,peak_speed.tolist())),peak_command_torque_nm=dict(zip(names,max_tau.tolist())),
        torque_saturated_fraction=sat/(duration/.002),max_torque_ratio=float(max(max_tau/limits)),
        knee_transfer_started_s=kneel_start,contact_gated=contact_gated,
        impact_proxies=impact,impact_note='Numerical ground-contact proxies, not damage predictions. Region forces sum simultaneous contacts. Contact speed is sampled during contact, not an exact pre-impact measurement. First-100ms impulse includes support load. Head contact is allowed in comparative evaluation.',
        torso_com_peak_sampled_acceleration_m_s2=peak_torso_acceleration,
        torso_measurement_note='Torso shell mesh only, excluding head and waist support. Front/back uses torso-local contact x sign (approximate). COM acceleration is a 2ms finite difference of rigid-body COM velocity, not electronics acceleration or a damage threshold.',
        hug_ik_target_error_m=hug_errors,final_hand_to_knee_target_distance_m=hand_knee_distances,
        knees_before_hands=bool(max(first_knees)<first_hands),knees_before_upper_body=knee_order,arms_release_s=arms_release,
        first_head_contact_s=next((e['time'] for e in events if e['event']=='head_ground_contact'),None),
        knee_first_geometry_pass=bool(knee_order and not head_impact and knees>30 and final_speed<.15 and -max_self<.02),
        candidate_pass=bool(not head_impact and knees>30 and hands>20 and final_speed<.15 and -max_self<.02),
        acceptance_note='candidate_pass checks final support geometry only; it is not a contact-impact, robustness or hardware safety qualification',
        scope='Free-base forward dynamics with original mesh collisions; simulation-only prototype; no hardware validity claim',
        provenance=dict(mujoco_version=mujoco.__version__,scene=str(MODEL),included_model_sha256=hashlib.sha256((MODEL.parent/'g1_29dof_old.xml').read_bytes()).hexdigest(),checkpoint=str(CHECKPOINT),checkpoint_sha256=hashlib.sha256(CHECKPOINT.read_bytes()).hexdigest(),controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),dt_s=.002,video_sample_dt_s=.02,initial_state='saved SONIC simulated standing checkpoint',physical_robot_access=False))
    if out:
        out=Path(out);out.mkdir(parents=True,exist_ok=True);np.save(out/'qpos.npy',frames);(out/'trace.json').write_text(json.dumps(trace));(out/'report.json').write_text(json.dumps(result,indent=2));(out/'events.json').write_text(json.dumps(events,indent=2))
    return result

def render(directory):
    directory=Path(directory);m,d,*_=setup();frames=np.load(directory/'qpos.npy');renderer=mujoco.Renderer(m,height=480,width=640)
    trace=json.loads((directory/'trace.json').read_text());segments=[]
    for row in trace:
        if not segments or segments[-1][2]!=row['phase']:segments.append([row['time'],row['time']+.02,row['phase']])
        else:segments[-1][1]=row['time']+.02
    labels=dict(sit_back='2. Sit back with arms approaching knees',tuck_roll='3. Tuck and roll backward',hold_tuck='4. Hold compact target (see measured outcome)',output_lost='SONIC output lost: configured low-level response',tip_for_knees='Knee-first candidate: shift support',crouch='1. Bend knees and lower body',reach_ground='2. Fold torso and establish hand support',lower_knees='3. Transfer support and lower knees',hold='4. Hold target posture (see outcome report)')
    def stamp(t):
        ms=round(t*1000);return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'
    subtitles=directory/'phases.srt'
    subtitles.write_text('\n\n'.join(f'{i+1}\n{stamp(a)} --> {stamp(b)}\n{labels.get(p,p)}' for i,(a,b,p) in enumerate(segments)))
    vf=f"drawtext=text='OFFLINE MuJoCo prototype | simulated SONIC standing | 1x speed':x=20:y=16:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.65,subtitles={subtitles}"
    report=json.loads((directory/'report.json').read_text())
    if report['config'].get('knee_first'):
        status='Impact comparison - head contact allowed - see measured loads'
        vf+=f",drawtext=text='{status}':x=20:y=48:fontsize=21:fontcolor=yellow:box=1:boxcolor=black@0.65"
    process=subprocess.Popen(['ffmpeg','-y','-loglevel','error','-f','rawvideo','-vcodec','rawvideo','-pix_fmt','rgb24','-s','1280x480','-r','50','-i','-','-vf',vf,'-an','-c:v','libx264','-crf','20','-pix_fmt','yuv420p',str(directory/'simulation.mp4')],stdin=subprocess.PIPE)
    cameras=[]
    for az in (90,135):
        cam=mujoco.MjvCamera();cam.type=mujoco.mjtCamera.mjCAMERA_FREE;cam.lookat[:]=[.2,0,.45];cam.distance=2.3;cam.azimuth=az;cam.elevation=-18;cameras.append(cam)
    try:
        for q in frames:
            d.qpos[:]=q;mujoco.mj_forward(m,d);images=[]
            for cam in cameras:renderer.update_scene(d,camera=cam);images.append(renderer.render().copy())
            process.stdin.write(np.concatenate(images,axis=1).tobytes())
    finally:process.stdin.close();process.wait();renderer.close()
    if process.returncode:raise RuntimeError('Video encoder failed')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--config',default='{}');p.add_argument('--render',action='store_true');a=p.parse_args()
    if a.render:render(a.output)
    else:print(json.dumps(simulate(json.loads(a.config),a.output),indent=2))
