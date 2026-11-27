"""Offline samples only. No network, DDS, control service or motor output."""
import argparse,json,time
from pathlib import Path
import numpy as np
from native_model import NativeModel
from support import SupportAllocator
p=argparse.ArgumentParser();p.add_argument('inputs',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
root=Path(__file__).resolve().parent;m=NativeModel(root/'model.json');solver=SupportAllocator(m.spec['simulation_effort_limits_nm']);data=np.load(a.inputs);times=[];worst=0.;torques=[]
for repeat in range(5):
 for q,quat,expected in zip(data['q'],data['quat'],data['ff']):
  begin=time.perf_counter();state=m.calculate(q,np.zeros(29),quat,np.zeros(3));result=solver.solve(state,np.zeros(29));times.append((time.perf_counter()-begin)*1000)
  worst=max(worst,float(np.max(abs(expected-result['feedforward']))))
r=dict(samples=len(times),mean_ms=float(np.mean(times)),p99_ms=float(np.percentile(times,99)),max_ms=max(times),max_reference_error_nm=worst,motor_commands_sent=0)
a.output.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));assert worst<1e-3
