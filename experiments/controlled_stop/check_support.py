"""Offline feasibility and rejection checks for the constrained allocator."""
from pathlib import Path
import numpy as np
from native_model import NativeModel
from controller import BASE,KP,KD
from support import SupportAllocator,InfeasibleSupport
root=Path(__file__).resolve().parent;m=NativeModel(root/'model.json')
r=m.calculate(BASE,np.zeros(29),np.array([1.,0,0,0]),np.zeros(3));limits=np.asarray(m.spec['simulation_effort_limits_nm'])
s=SupportAllocator(limits);sol=s.solve(r,np.zeros(29))
assert sol['base_residual']<1e-5 and sol['max_inequality_violation']<=1e-5
assert np.all(abs(sol['feedforward'])<=limits+1e-5)
# No downward pulling on the ground can balance negative required weight.
for name in ('negative_weight','impossible_horizontal_force','invalid_contact','nonfinite'):
 bad={k:v.copy() if hasattr(v,'copy') else v for k,v in r.items()}
 if name=='negative_weight':bad['bias'][2]=-100.
 elif name=='impossible_horizontal_force':bad['bias'][0]=10000.
 elif name=='invalid_contact':bad['feet'][0,2]+=.1
 else:bad['bias'][0]=np.nan
 try:s.solve(bad,np.zeros(29))
 except InfeasibleSupport:pass
 else:raise AssertionError('Accepted '+name)
print('feasible support bounds and 4 rejection cases passed; no hardware access')
