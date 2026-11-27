"""Coordinated-stage software checks from actual recorded states, no transport."""
import copy
import json
from pathlib import Path
import sys
import numpy as np
from controller import Controller,KP,KD,smooth
from native_model import NativeModel
from standing_plan import generate as shallow
from coordinated_plan import generate


def main(standing,holding):
    a=json.loads(Path(standing).read_text())['raw_feedback'];sample=json.loads(Path(holding).read_text());b=sample['raw_feedback']
    q0=np.array([m['q'] for m in a['motor_state'][:29]]);q=np.array([m['q'] for m in b['motor_state'][:29]])
    quat=np.array(b['imu_state']['quaternion']);model=NativeModel(Path(__file__).with_name('model.json'))
    model.spec['simulation_effort_limits_nm']=[35.]*29
    first,_=shallow(model,q0,a['imu_state']['quaternion']);plan,report=generate(model,q,quat,depth=.17)
    c=Controller.handoff(model,first,q0,np.zeros(29),np.column_stack((q0,np.zeros(29),np.zeros(29),KP,KD)),0.,recovery_enabled=True,trial_stage='handoff_hold')
    c.phase='holding';c.advance_shallow(first,3.,q0,np.zeros(29),a['imu_state']['quaternion'])
    c.phase='holding';c.motion_time=6.;c.stage_hold_since=8.;c.last=11.996;c.previous=np.asarray(sample['command'])
    c.tick(12.,q,np.zeros(29),quat,np.zeros(3))
    baseline=copy.deepcopy(c,{id(model):model});before=c.pose_at(6.).copy()
    for kind in ('too_early','moving','posture','depth','tilt','speed'):
        bad=copy.deepcopy(c,{id(model):model});p={k:v.copy() for k,v in plan.items()};v=q.copy();dq=np.zeros(29);now=12.
        if kind=='too_early':now=8.5
        if kind=='moving':dq[3]=.21
        if kind=='posture':v[3]+=.04
        if kind=='depth':p['depth_m']=np.array(.4)
        if kind=='tilt':p['quaternion'][-1]=[.8,.6,0,0]
        if kind=='speed':p['q'][20,3]+=.1
        try:bad.advance_coordinated(p,now,v,dq,quat)
        except ValueError:pass
        else:raise AssertionError('Accepted '+kind)
        assert bad.trial_stage=='measured_shallow'
    c.advance_coordinated(plan,12.,q,np.zeros(29),quat)
    np.testing.assert_allclose(c.pose_at(0),before,atol=1e-12)
    output=c.tick(12.004,q,np.zeros(29),quat,np.zeros(3))
    untouched=baseline.tick(12.004,q,np.zeros(29),quat,np.zeros(3))
    jump=float(np.max(abs(output[:,0]-untouched[:,0])))
    assert jump<1e-4
    paused=copy.deepcopy(c,{id(model):model});paused.tick(12.008,q,np.zeros(29),quat,np.zeros(3),heartbeat=False)
    assert paused.phase=='paused';at=paused.motion_time
    paused.tick(12.012,q,np.zeros(29),quat,np.zeros(3),heartbeat=True)
    assert paused.phase=='paused' and paused.motion_time==at
    # Prescribed feedback fixture tests bounds and terminal hold; it is not physics.
    previous=q.copy()
    for i in range(2,7001):
        t=min(i*.004,24.);target=c.pose_at(t)+(q-before)*(1-smooth(t/2.))
        qt=np.array([np.interp(t,plan['time'],plan['quaternion'][:,j]) for j in range(4)]);qt/=np.linalg.norm(qt)
        out=c.tick(12+i*.004,target,(target-previous)/.004,qt,np.zeros(3))
        assert np.isfinite(out).all();previous=target
    assert c.phase=='holding' and c.motion_time==24. and c.trial_stage=='measured_coordinated'
    try:c.advance_coordinated(plan,41.,previous,np.zeros(29),qt)
    except ValueError:pass
    else:raise AssertionError('Repeated coordinated advance accepted')
    print(json.dumps(dict(admission_rejections=6,target_jump_rad=jump,heartbeat_pause_latched=True,
        end_holds=True,repeat_rejected=True,source='actual recorded shallow hold',
        max_target_speed_rad_s=report['max_target_speed_rad_s'],scope='software_and_kinematics_not_physics'),indent=2))


if __name__=='__main__':main(*sys.argv[1:])
