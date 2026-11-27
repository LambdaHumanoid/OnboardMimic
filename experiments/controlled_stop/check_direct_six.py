"""No hardware: actual SONIC command continuity and prescribed-feedback checks."""
import copy,json,sys,time
from pathlib import Path
import numpy as np
from native_model import NativeModel
from direct_controller import DirectController,DirectTrajectory


def main(source_path,plan_path):
    source=json.loads(Path(source_path).read_text());f=source['raw_feedback'];q0=np.array([m['q'] for m in f['motor_state'][:29]]);quat0=np.array(f['imu_state']['quaternion'])
    command=np.asarray(source['command']);model=NativeModel(Path(__file__).with_name('model.json'))
    profile=json.loads(Path('/home/oem/jiangyiming/OnboardMimic_builds/controlled-stop-20261116/official-effort-comparison.json').read_text()) if Path('/home/oem/jiangyiming/OnboardMimic_builds/controlled-stop-20261116/official-effort-comparison.json').exists() else json.loads(Path(__file__).with_name('effort-profile.json').read_text())
    model.spec['simulation_effort_limits_nm']=[min(x['urdf_effort_nm'],x['simulation_effort_nm']) for x in profile['joints']]
    with np.load(plan_path) as p:plan={k:p[k].copy() for k in p.files}
    prepared=DirectTrajectory(plan,np.asarray(model.spec['limits']))
    c=DirectController.direct_handoff(model,plan,q0,np.zeros(29),quat0,command,0.,prepared=prepared)
    np.testing.assert_array_equal(c.held,command)
    for kind in ('moving','posture','orientation'):
        q=q0.copy();dq=np.zeros(29);quat=quat0.copy()
        if kind=='moving':dq[3]=.21
        if kind=='posture':q[3]+=.09
        if kind=='orientation':quat=np.array([0.,1.,0.,0.])
        try:DirectController.direct_handoff(model,plan,q,dq,quat,command,0.,prepared=prepared)
        except ValueError:pass
        else:raise AssertionError('Admission accepted '+kind)
    previous=command.copy();max_step=0.;max_ratio=0.;timing=[];velocity_seen=False
    for i in range(1,2001):
        t=i*.004;q,dq=prepared.sample(min(t,6.));q[15:]=q0[15:];dq[15:]=0.
        quat=np.array([np.interp(min(t,6.),plan['time'],plan['quaternion'][:,j]) for j in range(4)]);quat/=np.linalg.norm(quat)
        before=time.perf_counter();out=c.tick(t,q,dq,quat,np.zeros(3));timing.append((time.perf_counter()-before)*1000)
        max_step=max(max_step,float(np.max(abs(out[:,0]-previous[:,0]))));previous=out.copy()
        total=out[:,2]+out[:,3]*(out[:,0]-q)+out[:,4]*(out[:,1]-dq)
        max_ratio=max(max_ratio,float(np.max(abs(total)/model.spec['simulation_effort_limits_nm'])))
        velocity_seen=velocity_seen or bool(np.max(abs(out[:,1]))>.1)
        if i==500:
            paused=copy.deepcopy(c,{id(model):model});at=paused.motion_time
            paused.tick(t+.004,q,dq,quat,np.zeros(3),heartbeat=False)
            paused.tick(t+.008,q,dq,quat,np.zeros(3),heartbeat=True)
            assert paused.phase=='paused' and paused.motion_time==at
        if i==1501:assert c.phase=='holding' and c.motion_time==6.
    assert c.phase=='holding' and c.motion_time==6. and velocity_seen and max_step<=.00600001 and max_ratio<=1.000001
    assert np.max(abs(out[:,1]))==0.
    print(json.dumps(dict(held_command_exact=True,admission_rejections=3,motion_duration_s=c.motion_time,
        end_holds=True,heartbeat_pause_latched=True,velocity_feedforward=True,max_command_step_rad=max_step,
        max_sampled_effort_ratio=max_ratio,compute_p99_ms=float(np.percentile(timing,99)),compute_max_ms=max(timing),
        scope='prescribed feedback software test, not physical balance validation'),indent=2))


if __name__=='__main__':main(*sys.argv[1:])
