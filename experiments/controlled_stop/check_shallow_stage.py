"""Software admission/continuity checks; never connects to a robot."""
import copy
import json
from pathlib import Path
import sys
import numpy as np
from controller import Controller, KP, KD
from native_model import NativeModel
from standing_plan import generate


def main(sample_path):
    frame=json.loads(Path(sample_path).read_text())['raw_feedback']
    q=np.array([m['q'] for m in frame['motor_state'][:29]])
    quat=np.array(frame['imu_state']['quaternion'])
    model=NativeModel(Path(__file__).with_name('model.json'))
    model.spec['simulation_effort_limits_nm']=[35.]*29
    plan,report=generate(model,q,quat)
    command=np.column_stack((q,np.zeros(29),np.zeros(29),KP,KD))
    c=Controller.handoff(model,plan,q,np.zeros(29),command,0.,trial_stage='handoff_hold',recovery_enabled=True)
    for i in range(1,751):c.tick(i*.004,q,np.zeros(29),quat,np.zeros(3))
    assert c.phase=='holding' and c.motion_time==0
    before=c.pose_at(0)
    for kind in ('posture','moving','early','plan_speed','orientation'):
        bad=copy.deepcopy(c,{id(model):model});args=[plan,3.,q.copy(),np.zeros(29),quat.copy()]
        if kind=='posture':args[2][3]+=.04
        if kind=='moving':args[3][3]=.21
        if kind=='early':args[1]=1.
        if kind=='plan_speed':
            args[0]={k:v.copy() for k,v in plan.items()};args[0]['q'][10,3]+=.1
        if kind=='orientation':args[4]=np.array([0.,1.,0.,0.])
        try:bad.advance_shallow(*args)
        except ValueError:pass
        else:raise AssertionError('Admission accepted '+kind)
        assert bad.trial_stage=='handoff_hold' and bad.motion_time==0
    c.advance_shallow(plan,3.,q,np.zeros(29),quat)
    np.testing.assert_allclose(c.pose_at(0),before,atol=1e-12)
    assert np.max(abs(c.pose_at(.004)-before))<.001
    wrist=Controller.handoff(model,plan,q,np.zeros(29),command,0.,trial_stage='handoff_hold',recovery_enabled=True)
    wrist.phase='holding';wrist.start[26]+=.12
    wrist.advance_shallow(plan,3.,q,np.zeros(29),quat)
    assert wrist.pose_at(0)[26]==wrist.start[26] and wrist.pose_at(6)[26]==wrist.start[26]
    paused=copy.deepcopy(c,{id(model):model})
    paused.tick(3.004,q,np.zeros(29),quat,np.zeros(3),heartbeat=False)
    assert paused.phase=='paused' and paused.motion_time==0
    paused.tick(3.008,q,np.zeros(29),quat,np.zeros(3),heartbeat=True)
    assert paused.phase=='paused' and paused.motion_time==0
    # Kinematic tracking fixture is explicitly not a physics/balance simulation.
    prev=q.copy()
    for i in range(1,1751):
        target=c.pose_at(min(i*.004,6.))
        dq=(target-prev)/.004
        out=c.tick(3.+i*.004,target,dq,quat,np.zeros(3))
        assert np.isfinite(out).all()
        prev=target
    assert c.phase=='holding' and c.motion_time==6.
    try:c.advance_shallow(plan,10.,prev,np.zeros(29),quat)
    except ValueError:pass
    else:raise AssertionError('Repeated advance accepted')
    print(json.dumps(dict(admission_rejections=5,continuous_start=True,heartbeat_pause_latched=True,
        one_shot=True,holds_at_end=True,kinematic_report=report),indent=2))


if __name__=='__main__':main(sys.argv[1])
