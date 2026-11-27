"""Offline diagnostic threshold and independent speed-guard checks."""
from pathlib import Path
import numpy as np
from controller import Controller,BASE,KP,KD
class Model:
    spec={'limits':[[-3,3]]*29,'joint_names':['joint_'+str(i) for i in range(29)]}
    def calculate(self,*args):return dict(feet=np.zeros((2,3)),com=np.zeros(3),feedforward=np.ones(29)*41)
m=Model();trajectory=np.load(Path(__file__).with_name('trajectory.npz'))
command=np.column_stack((BASE,np.zeros(29),np.ones(29)*41,KP,KD))
c=Controller.handoff(m,trajectory,BASE,np.zeros(29),command,0)
out=c.tick(0,BASE,np.zeros(29),np.array([1.,0,0,0]),np.zeros(3))
assert c.phase=='lowering' and c.fault_context is None
assert c.compensation_event['first_requested_nm']==41
assert c.compensation_event['response']=='diagnostic_only'
assert np.all(out[:,2]==41), 'Historical threshold still clamps output'
print('41 Nm produces a warning without stop, pause or 40 Nm clamp')
c=Controller.handoff(m,trajectory,BASE,np.zeros(29),command,0)
dq=np.zeros(29);dq[10]=4.1
try:c.tick(.004,BASE,dq,np.array([1.,0,0,0]),np.zeros(3))
except ValueError:
    assert c.fault_context['joint']==10
    assert c.fault_context['measured_rad_s']==4.1
    assert len(c.fault_context['previous_command'])==29
else:raise AssertionError('Speed guard bypassed')
c=Controller.handoff(m,trajectory,BASE,np.zeros(29),command,0)
c.motion_time=.25;c.pause_progress('recording_unhealthy')
assert c.phase=='paused' and c.motion_time==.25
assert c.fault_context['response']=='freeze_progress'
print('speed fault evidence and latched soft pause passed')
