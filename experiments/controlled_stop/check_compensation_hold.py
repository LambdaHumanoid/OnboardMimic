"""Fault injection without transport: over-limit raw FF must not end output."""
from pathlib import Path
import numpy as np
from controller import Controller,BASE,KP,KD
from native_model import NativeModel
root=Path(__file__).resolve().parent
base=NativeModel(root/'model.json')
class Injected:
    spec=base.spec
    ff=0.
    def calculate(self,*args):
        r=base.calculate(*args)
        if self.ff:r['feedforward'][4]=self.ff
        return r
m=Injected();trajectory=np.load(root/'trajectory.npz')
q=BASE.copy();dq=np.zeros(29);quat=np.array([1.,0,0,0]);gyro=np.zeros(3)
initial=base.calculate(q,dq,quat,gyro)
command=np.column_stack((q,np.zeros(29),initial['feedforward'],KP,KD))
c=Controller.handoff(m,trajectory,q,dq,command,0,recovery_enabled=True,constrained_support=True,trial_stage='handoff_hold')
outputs=[]
for i in range(750):
    t=i*.004
    m.ff=41. if 125<=i<250 else (80. if 250<=i<500 else 0.)
    out=c.tick(t,q,dq,quat,gyro)
    outputs.append(out)
    assert np.isfinite(out).all() and np.all(out[:,3]>0)
    assert np.all(abs(c.diagnostics['estimated_total_target_nm'])<=c.effort_limits+1e-6)
    if i>=125:
        assert c.phase=='holding' and c.motion_time==0
        assert c.fault_context is None
assert len(outputs)==750
assert c.compensation_event['exceeding_samples']==375
assert c.compensation_event['first_requested_nm']==41.
assert c.compensation_event['peak_abs_nm']==80.
assert c.compensation_event['joint_name']=='left_ankle_pitch'
assert c.compensation_event['last_at']==499*.004
# Warning persists for inspection without altering the hold-only trial.
assert c.diagnostics['compensation_event']['peak_abs_nm']==80.
print('750 continuous outputs; 375 over-limit samples; first/peak joint evidence; diagnostic-only warning; bounds and positive stiffness passed')
