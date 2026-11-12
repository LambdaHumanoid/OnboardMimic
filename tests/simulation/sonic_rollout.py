"""Real SONIC + MuJoCo rollout, restricted to an isolated loopback namespace.

Requires separately installed MuJoCo and a matching G1 29-DOF scene. No DDS.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--case', choices=('stand', 'walk', 'turn'), default='stand')
    p.add_argument('--duration', type=float, default=10)
    a = p.parse_args()
    parent = os.environ.get('ONBOARDMIMIC_PARENT_NETNS')
    current = os.readlink('/proc/self/ns/net')
    if not parent or parent == current or {name for _, name in socket.if_nameindex()} != {'lo'}:
        p.error('Requires a separate --network none container; never run on a robot network')
    if not 1 <= a.duration <= 60:
        p.error('duration must be 1..60 seconds')
    if a.output.exists():p.error('Use a new output directory')
    import numpy as np
    import mujoco
    import zmq
    import msgpack
    from onboardmimic.backends.sonic.wire import build_command_message, build_planner_message
    a.output.mkdir(parents=True)
    deploy=ROOT/'onboardmimic/backends/sonic/deploy'
    binary=deploy/'target/release/g1_deploy_onnx_ref'
    q0=np.asarray(json.loads((deploy/'onboard/baseline_q.json').read_text()))
    names=[s+'_'+n for s in ('left','right') for n in ('hip_pitch','hip_roll','hip_yaw','knee','ankle_pitch','ankle_roll')]+['waist_yaw','waist_roll','waist_pitch']+[s+'_'+n for s in ('left','right') for n in ('shoulder_pitch','shoulder_roll','shoulder_yaw','elbow','wrist_roll','wrist_pitch','wrist_yaw')]
    model=mujoco.MjModel.from_xml_path(str(a.model));model.opt.timestep=.002;data=mujoco.MjData(model)
    joints=[model.joint(n+'_joint').id for n in names];qa=model.jnt_qposadr[joints];va=model.jnt_dofadr[joints]
    actuators=np.asarray([list(model.actuator_trnid[:,0]).index(j) for j in joints]);torso=model.body('torso_link').id
    data.qpos[:7]=[0,0,.78874,1,0,0,0];data.qpos[qa]=q0;mujoco.mj_forward(model,data)
    context=zmq.Context();state=context.socket(zmq.PUB);state.bind('tcp://127.0.0.1:15660')
    sink=context.socket(zmq.SUB);sink.setsockopt(zmq.SUBSCRIBE,b'rt/lowcmd');sink.bind('tcp://127.0.0.1:15661')
    commands=context.socket(zmq.PUB);commands.bind('tcp://127.0.0.1:15686')
    args=[str(binary),'lo','policy/release/model_decoder.onnx','reference/example/','--obs-config','policy/release/observation_config.yaml','--encoder-file','policy/release/model_encoder.onnx','--planner-file','planner/target_vel/V2/planner_orin_compatible.onnx','--input-type','zmq_manager','--output-type','zmq','--hand-type','none','--zmq-host','127.0.0.1','--zmq-port','15686','--zmq-out-port','15687','--zmq-robot-ip','127.0.0.1','--zmq-robot-state-port','15660','--zmq-robot-cmd-port','15661','--zmq-robot-lowcmd-udp-port','0']
    logfile=a.output/'controller.log';log=logfile.open('w')
    env=dict(os.environ,SONIC_SIM_GESTURES='0',HAND_TYPE='none');env.pop('SONIC_LOWCMD_ZMQ_FALLBACK',None)
    child=subprocess.Popen(args,cwd=deploy,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    start=time.monotonic();deadline=start;active=None;last=None;last_rx=start;next_cmd=start;next_log=start
    prepared=False;count=0;step=0;trace=[];error=None;heading=0;max_lag=0
    try:
        while time.monotonic()-start<90:
            now=time.monotonic()
            if child.poll() is not None:raise RuntimeError('Controller exited: '+str(child.returncode))
            while True:
                try:
                    parts=sink.recv_multipart(zmq.NOBLOCK)
                    values=msgpack.unpackb(parts[1],raw=False)['motor_cmd'][:29]
                    last=np.asarray([[v[k] for k in ('q','dq','tau','kp','kd')] for v in values]);last_rx=now;count+=1
                    if last.shape!=(29,5) or not np.isfinite(last).all():raise RuntimeError('Invalid command')
                except zmq.Again:break
            if now>=next_log:
                text=logfile.read_text(errors='replace');next_log=now+.1
                prepared=prepared or 'Init Done' in text
                if active is None and 'Bumpless policy handoff complete' in text:active=now
            moving=active is not None and 4<=now-active<4+a.duration
            if now>=next_cmd:
                next_cmd=now+.02
                if a.case=='turn' and moving:heading+=.15*.02
                commands.send(build_planner_message(1 if moving and a.case=='walk' else 0,[1,0,0] if moving and a.case=='walk' else [0,0,0],[math.cos(heading),math.sin(heading),0],speed=.3 if moving and a.case=='walk' else 0))
                if prepared and active is None:commands.send(build_command_message(True,False,True))
            if last is not None and now-last_rx<.1:
                q,dq,tau,kp,kd=last.T
                data.ctrl[actuators]=np.clip(tau+kp*(q-data.qpos[qa])+kd*(dq-data.qvel[va]),model.actuator_ctrlrange[actuators,0],model.actuator_ctrlrange[actuators,1])
            else:data.ctrl[actuators]=-2*data.qvel[va]
            if active is None:
                data.qpos[:7]=[0,0,.78874,1,0,0,0];data.qvel[:6]=0
                if last is None:data.qpos[qa]=q0;data.qvel[va]=0
            mujoco.mj_step(model,data);step+=1
            if not np.isfinite(data.qpos).all():raise RuntimeError('Nonfinite physics')
            if active is not None:
                w,x,y,z=data.xquat[torso];tilt=math.degrees(math.acos(max(-1,min(1,1-2*(x*x+y*y)))))
                trace.append([now-active,float(data.time),float(data.qpos[0]),float(data.qpos[1]),float(data.qpos[2]),tilt,float(np.max(np.abs(data.qvel[va])))])
                if data.qpos[2]<.4 or tilt>45:raise RuntimeError('Simulated fall')
                if now-active>=a.duration+8:break
            if step%2==0:
                imu=dict(quaternion=data.qpos[3:7].tolist(),gyroscope=data.qvel[3:6].tolist(),accelerometer=[0.,0.,9.81],rpy=[0.,0.,0.])
                motors=[dict(q=float(q),dq=float(v),motorstate=0,mode=1) for q,v in zip(data.qpos[qa],data.qvel[va])]+[dict(q=0.,dq=0.,motorstate=0,mode=1) for _ in range(6)]
                state.send_multipart([b'rt/lowstate',msgpack.packb(dict(tick=step,mode_machine=5,imu_state=imu,motor_state=motors,wireless_remote=[0]*40),use_bin_type=True)])
                vel=np.zeros(6);mujoco.mj_objectVelocity(model,data,mujoco.mjtObj.mjOBJ_BODY,torso,vel,0)
                state.send_multipart([b'rt/secondary_imu',msgpack.packb(dict(imu,quaternion=data.xquat[torso].tolist(),gyroscope=vel[:3].tolist()),use_bin_type=True)])
            deadline+=.002;lag=time.monotonic()-deadline;max_lag=max(max_lag,lag)
            if lag>.02:deadline=time.monotonic()
            time.sleep(max(0,deadline-time.monotonic()))
        else:raise RuntimeError('Timeout')
    except Exception as exc:error=str(exc)
    finally:
        if child.poll() is None:child.terminate()
        try:child.wait(timeout=3)
        except subprocess.TimeoutExpired:child.kill();child.wait()
        log.close()
        for sock in (state,sink,commands):sock.close(0)
        context.term()
    result=dict(case=a.case,passed=error is None,error=error,physical_motion=False,network_isolated=True,binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),mujoco=mujoco.__version__,lowcmd_count=count,wall_s=time.monotonic()-start,sim_s=float(data.time),max_scheduler_lag_ms=max_lag*1000,
                max_joint_speed_rad_s=max((r[6] for r in trace),default=None),max_tilt_deg=max((r[5] for r in trace),default=None),min_height_m=min((r[4] for r in trace),default=None))
    (a.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    np.save(a.output/'trace.npy',np.asarray(trace))
    print(json.dumps(result),flush=True)
    return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
