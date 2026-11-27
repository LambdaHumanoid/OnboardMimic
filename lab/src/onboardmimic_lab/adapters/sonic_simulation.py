"""CPU ONNX SONIC decoder + local MuJoCo reference rollout. No robot transport."""
from collections import deque
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from ..dataset import digest, write_json
from ..layout import G1_JOINT_NAMES
from ..replay import validate_plan, summary
from .simulation_models import verify_models
from ..sonic_parameters import read_parameters


class Decoder:
    def __init__(self, config):
        import numpy as np
        import onnxruntime as ort
        import yaml
        self.np = np
        self.params = {k: np.asarray(v) for k, v in read_parameters(config['policy_parameters']).items()}
        spec = yaml.safe_load(Path(config['observation_config']).read_text())
        self.fields = []
        for field in spec['observations']:
            if not field.get('enabled', True): continue
            name = field['name']
            if name == 'token_state':
                self.fields.append(('token', 1)); continue
            match = re.fullmatch(r'his_(base_angular_velocity|body_joint_positions|body_joint_velocities|last_actions|gravity_dir)_(4|10)frame_step1', name)
            if not match: raise ValueError('unsupported decoder observation: '+name)
            self.fields.append((match[1], int(match[2])))
        if sum(k == 'token' for k, n in self.fields) != 1:
            raise ValueError('decoder must have exactly one token observation')
        self.history = deque(maxlen=max(n for k, n in self.fields))
        options = ort.SessionOptions(); options.intra_op_num_threads = 1; options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(config['decoder_model'], sess_options=options, providers=['CPUExecutionProvider'])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        dim = sum(64 if k == 'token' else (3 if k in ('base_angular_velocity', 'gravity_dir') else 29)*n for k, n in self.fields)
        if (len(inputs) != 1 or inputs[0].shape != [1, dim] or inputs[0].type != 'tensor(float)'
                or len(outputs) != 1 or outputs[0].shape != [1, 29]):
            raise ValueError('decoder tensor contract mismatch; expected float32 [1,%d] -> [1,29]' % dim)
        self.input_name, self.output_name = inputs[0].name, outputs[0].name
        self.last_action = np.zeros(29)
        self.standing_encoder = None

    def standing_token(self, rotation):
        """Local G1 standing reference, decoded against simulated feedback.

        This is a simulation prelude, not a recorded token or a Planner replica.
        The 1762-D G1 encoder layout is checked before using its fixed offsets.
        """
        if self.standing_encoder is None:
            raise ValueError('SONIC standing encoder is unavailable')
        np=self.np;obs=np.zeros((1,1762),dtype=np.float32)
        obs[0,4:294]=np.tile(self.params['default_angles'][self.params['mujoco_to_isaaclab']],10)
        obs[0,601:661]=np.tile(rotation.T[:,:2].reshape(-1),10)
        token=self.standing_encoder.run(None,{self.standing_encoder.get_inputs()[0].name:obs})[0].reshape(64)
        if not np.isfinite(token).all():raise ValueError('nonfinite standing token')
        return token

    def enable_standing_encoder(self, config):
        import onnxruntime as ort
        import yaml
        names=[f['name'] for f in yaml.safe_load(Path(config['observation_config']).read_text())['encoder']['encoder_observations'] if f.get('enabled',True)]
        expected=['encoder_mode_4','motion_joint_positions_10frame_step5','motion_joint_velocities_10frame_step5',
                  'motion_root_z_position_10frame_step5','motion_root_z_position','motion_anchor_orientation',
                  'motion_anchor_orientation_10frame_step5','motion_joint_positions_lowerbody_10frame_step5',
                  'motion_joint_velocities_lowerbody_10frame_step5','vr_3point_local_target','vr_3point_local_orn_target',
                  'smpl_joints_10frame_step1','smpl_anchor_orientation_10frame_step1','motion_joint_positions_wrists_10frame_step1']
        if names!=expected:raise ValueError('unsupported standing encoder layout')
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        self.standing_encoder=ort.InferenceSession(config['encoder_model'],sess_options=options,providers=['CPUExecutionProvider'])
        if self.standing_encoder.get_inputs()[0].shape!=[1,1762] or self.standing_encoder.get_outputs()[0].shape!=[1,64]:
            raise ValueError('standing encoder tensor contract mismatch')

    def observe(self, q, dq, angular_velocity, gravity):
        p = self.params; order = p['mujoco_to_isaaclab']
        entry = dict(base_angular_velocity=angular_velocity.copy(),
                     body_joint_positions=(q-p['default_angles'])[order],
                     body_joint_velocities=dq[order], last_actions=self.last_action.copy(), gravity_dir=gravity.copy())
        if not self.history:
            for _ in range(self.history.maxlen): self.history.append(entry)
        else: self.history.append(entry)

    def step(self, token):
        np = self.np
        parts = [np.asarray(token) if key == 'token' else np.concatenate([h[key] for h in list(self.history)[-n:]])
                 for key, n in self.fields]
        observation = np.concatenate(parts).astype(np.float32).reshape(1, -1)
        if not np.isfinite(observation).all(): raise ValueError('nonfinite decoder input')
        action = self.session.run([self.output_name], {self.input_name: observation})[0].reshape(29)
        if not np.isfinite(action).all(): raise ValueError('nonfinite decoder action')
        self.last_action = action.copy()
        p = self.params
        return p['default_angles']+action[p['isaaclab_to_mujoco']]*p['g1_action_scale']


def run_simulation(plan, config, output, video=True):
    validate_plan(plan); verify_models(plan, config)
    return rollout(plan, config, output, video=video)


def reference_schedule(frames, period=20_000_000):
    """One token per control period; source offsets remain analysis evidence."""
    return [i*period for i in range(len(frames))]


def rollout(plan, config, output, video=True, decoder=None, extra_report=None):
    """Shared physics loop. Callers validate their own recording contract first."""
    import numpy as np
    # Set rendering backend before MuJoCo import. Physics has no socket/SDK imports.
    os.environ.setdefault('MUJOCO_GL', 'egl')
    import mujoco
    out = Path(output).resolve()
    if out.exists(): raise ValueError('simulation output already exists')
    decoder = decoder or Decoder(config)
    deadlines=reference_schedule(plan['frames'])
    decoder.enable_standing_encoder(config)
    model = mujoco.MjModel.from_xml_path(config['scene'])
    if model.nq != 36 or model.nv != 35 or model.nu != 29 or model.jnt_type[0] != mujoco.mjtJoint.mjJNT_FREE:
        raise ValueError('simulation requires a floating-base G1 29 DOF scene without hand actuators')
    joints = [model.joint(name).id for name in G1_JOINT_NAMES]
    qa, va = model.jnt_qposadr[joints], model.jnt_dofadr[joints]
    actuators = np.asarray([list(model.actuator_trnid[:, 0]).index(j) for j in joints])
    if (not np.all(model.actuator_gear[actuators, 0] == 1)
            or not np.all(model.actuator_gaintype[actuators] == mujoco.mjtGain.mjGAIN_FIXED)
            or not np.all(model.actuator_gainprm[actuators, 0] == 1)
            or not np.all(model.actuator_biastype[actuators] == mujoco.mjtBias.mjBIAS_NONE)):
        raise ValueError('scene must use unit-gear direct torque motors')
    if not np.all(model.actuator_ctrllimited[actuators]):
        raise ValueError('scene must declare actuator torque limits')
    height = config.get('initial_height_m', .79)
    if type(height) not in (float, int) or not math.isfinite(height) or not .5 <= height <= 1.2:
        raise ValueError('initial_height_m must be .5..1.2 (world height is not recorded)')
    data = mujoco.MjData(model)
    data.qpos[:3] = [0, 0, height]; data.qpos[3:7] = [1.,0.,0.,0.]
    data.qpos[qa] = decoder.params['default_angles']; data.qvel[:] = 0.
    first_q=np.asarray(plan['initial']['q'])
    first_rot=np.zeros(9);mujoco.mju_quat2Mat(first_rot,np.asarray(plan['initial']['quat_wxyz'],dtype=float))
    first_gravity=first_rot.reshape(3,3).T @ np.array([0.,0.,-1.])
    mujoco.mj_forward(model, data)
    out.mkdir(parents=True)
    report = dict(episode=plan['episode'],segment_index=plan['segment_index'],frames=len(plan['frames']), schema='onboardmimic.lab.simulation', schema_version=1, status='running',
                  physical_motion=False, decoder_backend='onnxruntime-cpu', mujoco=mujoco.__version__,
                  initial_height_m=height, initialization='default standing joints; upright base; zero velocity; simulated feedback history',
                  limitations='reference decoder+PD rollout; not the native TensorRT controller or its guards/transition logic',
                  inputs={k: dict(path=config[k], **digest(config[k])) for k in
                          ('encoder_model','decoder_model','observation_config','policy_parameters','scene') if k in config},
                  frames_completed=0, error=None)
    report['entry']=dict(warm_s=1.,blend_s=2.,stable_s=.2,timeout_s=6.,playback_start_s=None,
                        boundary='live first-token settled speed and attitude; not exact recorded-state reconstruction',
                        warm_controller='CPU encoder G1 standing reference; not native Planner')
    report['timing']=dict(mode='sequential_50hz',period_ns=20_000_000,
        source_duration_ns=plan['frames'][-1]['offset_ns']+20_000_000,scheduled_duration_ns=deadlines[-1]+20_000_000)
    report.update(extra_report or {})
    write_json(out/'result.json', report)
    renderer = writer = video_log = None
    start = time.monotonic(); errors = []; max_speed = max_tilt = 0.; min_height = height
    try:
        if video:
            renderer = mujoco.Renderer(model, height=480, width=640)
            camera = mujoco.MjvCamera(); camera.distance = 2.4; camera.azimuth = 135; camera.elevation = -15
            video_log = (out/'ffmpeg.log').open('w')
            writer = subprocess.Popen(['ffmpeg','-nostdin','-v','error','-f','rawvideo','-pixel_format','rgb24',
                '-video_size','640x480','-framerate','50','-i','pipe:0','-an','-c:v','libx264',
                '-pix_fmt','yuv420p',str(out/'replay.mp4')], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=video_log)
        next_video = .02
        with (out/'trajectory.jsonl').open('x') as trace:
            i=0;playback_start=None;stable_since=None;return_start=None;return_stable=None;held=decoder.params['default_angles'].copy()
            while i<len(plan['frames']):
                rot = np.zeros(9); mujoco.mju_quat2Mat(rot, data.qpos[3:7]); gravity = rot.reshape(3,3).T @ np.array([0.,0.,-1.])
                if playback_start is None and data.time>=3.-1e-9:
                    ready=(np.max(np.abs(data.qvel[va]))<=.6 and np.linalg.norm(gravity-first_gravity)<=.15)
                    stable_since=(data.time if stable_since is None else stable_since) if ready else None
                    report['entry'].update(joint_error_rad=float(np.max(np.abs(data.qpos[qa]-first_q))),
                                           command_tracking_error_rad=float(np.max(np.abs(data.qpos[qa]-target))),
                                           speed_rad_s=float(np.max(np.abs(data.qvel[va]))),gravity_error=float(np.linalg.norm(gravity-first_gravity)))
                    if stable_since is not None and data.time-stable_since>=.2-1e-9:
                        playback_start=data.time;report['entry']['playback_start_s']=playback_start
                    elif data.time>=6.-1e-9:raise ValueError('start_pose_transition_timeout')
                phase='playing' if playback_start is not None else 'warming' if data.time<1.-1e-9 else 'transitioning' if data.time<3.-1e-9 else 'aligning'
                if return_start is not None:phase='returning' if data.time-return_start<2.-1e-9 else 'settling_planner'
                if phase=='playing' and i+1<len(plan['frames']) and deadlines[i+1]/1e9<=data.time-playback_start+1e-9:i+=1
                frame=plan['frames'][i]
                decoder.observe(data.qpos[qa], data.qvel[va], data.qvel[3:6], gravity)
                token=decoder.standing_token(rot.reshape(3,3)) if phase=='warming' else frame['token']
                if return_start is not None:
                    progress=float(np.clip((data.time-return_start)/2.,0.,1.));weight=progress*progress*(3.-2.*progress)
                    token=(1.-weight)*np.asarray(frame['token'])+weight*decoder.standing_token(rot.reshape(3,3))
                    quiet=(np.max(np.abs(data.qvel[va]))<=.6 and np.linalg.norm(gravity-np.array([0.,0.,-1.]))<=.15)
                    return_stable=(data.time if return_stable is None else return_stable) if progress>=1. and quiet else None
                    if data.time-return_start>=6.-1e-9:raise ValueError('planner_return_timeout')
                target = decoder.step(token)
                if phase=='warming':
                    held=target.copy();end=data.time+.02
                elif phase in ('transitioning','aligning'):
                    progress=float(np.clip((data.time-1.)/2.,0.,1.));weight=progress*progress*(3.-2.*progress)
                    target=held*(1.-weight)+target*weight
                    # Last actions reflect the command sent through PD, as in native entry blending.
                    decoder.last_action=((target-decoder.params['default_angles'])/decoder.params['g1_action_scale'])[decoder.params['mujoco_to_isaaclab']]
                    end=data.time+.02
                else:
                    end = data.time+.02
                # Same fixed-rate decoder loop as native, including held tokens.
                while data.time < end-1e-10:
                    model.opt.timestep = min(.002, end-data.time)
                    p = decoder.params
                    tau = p['kps']*(target-data.qpos[qa])-p['kds']*data.qvel[va]
                    data.ctrl[actuators] = np.clip(tau, model.actuator_ctrlrange[actuators,0], model.actuator_ctrlrange[actuators,1])
                    mujoco.mj_step(model, data)
                    if not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all(): raise ValueError('nonfinite simulated state')
                    w,x,y,z = data.qpos[3:7]
                    tilt = math.degrees(math.acos(float(np.clip(1-2*(x*x+y*y), -1, 1))))
                    max_tilt = max(max_tilt, tilt); min_height = min(min_height, float(data.qpos[2]))
                    max_speed = max(max_speed, float(np.max(np.abs(data.qvel[va]))))
                    if data.qpos[2] < .4 or tilt > 45: raise ValueError('simulated fall; inspect trajectory and initial state')
                    if renderer and data.time+1e-9 >= next_video:
                        camera.lookat[:] = data.qpos[:3]
                        renderer.update_scene(data, camera=camera); writer.stdin.write(renderer.render().tobytes())
                        next_video += .02
                trace.write(json.dumps(dict(phase=phase,frame=i if phase=='playing' else None,
                    source_index=frame['source_index'] if phase=='playing' else None,time_s=float(data.time),
                    token=np.asarray(token).tolist(),q=data.qpos[qa].tolist(),dq=data.qvel[va].tolist(),
                    root=data.qpos[:7].tolist(),joint_target=target.tolist()),allow_nan=False)+'\n')
                if phase=='playing':
                    report['frames_completed'] = i+1
                    if i==len(plan['frames'])-1:return_start=data.time
                elif return_stable is not None and data.time-return_stable>=.2-1e-9:
                    report['planner_return']=dict(confirmed=True,start_s=return_start,end_s=float(data.time),
                        controller='CPU encoder standing reference; not native Planner',blend_s=2.)
                    break
    except BaseException as exc:
        errors.append(type(exc).__name__+': '+str(exc))
    finally:
        if writer:
            try:
                writer.stdin.close()
                if writer.wait(timeout=15) != 0: errors.append('ffmpeg failed; see ffmpeg.log')
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(str(exc)); writer.kill(); writer.wait()
        if renderer: renderer.close()
        if video_log: video_log.close()
        report.update(status='failed' if errors else 'completed', error='; '.join(errors) or None,
                      wall_s=time.monotonic()-start, simulated_s=float(data.time), max_joint_speed_rad_s=max_speed,
                      max_tilt_deg=max_tilt, min_height_m=min_height,
                      files={p.name:digest(p) for p in out.iterdir() if p.is_file() and p.name != 'result.json'})
        write_json(out/'result.json',report)
    if errors: raise ValueError('simulation failed: '+report['error']+'; report: '+str(out/'result.json'))
    return report
