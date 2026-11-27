"""Compute + encode only. No socket, DDS writer or hardware command."""
import json,time
from pathlib import Path
import numpy as np
from controller import Controller,BASE,KP,KD
from native_model import NativeModel
from wire import Encoder
root=Path(__file__).resolve().parent;m=NativeModel(root/'model.json');tr=np.load(root/'trajectory.npz');e=Encoder();command=np.column_stack((BASE,np.zeros(29),np.zeros(29),KP,KD));cost=[]
for i in range(2000):
 c=Controller.handoff(m,tr,BASE,np.zeros(29),command,0)
 start=time.perf_counter();out=c.tick(.004,BASE,np.zeros(29),np.array([1.,0,0,0]),np.zeros(3));packet=e.encode(out,0,777);cost.append((time.perf_counter()-start)*1000)
 assert len(packet)==931
r=dict(samples=len(cost),mean_ms=float(np.mean(cost)),p99_ms=float(np.percentile(cost,99)),max_ms=max(cost),motor_commands_sent=0)
print(json.dumps(r));assert r['p99_ms']<4
