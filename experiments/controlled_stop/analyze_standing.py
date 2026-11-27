"""Offline analysis of read-only capture, with source-time pairing."""
import argparse,json,msgpack,time
from pathlib import Path
import numpy as np
from native_model import NativeModel
from model import rotation
from support import SupportAllocator,InfeasibleSupport
p=argparse.ArgumentParser();p.add_argument('capture',type=Path);a=p.parse_args();root=Path(__file__).resolve().parent
m=NativeModel(root/'model.json');solver=SupportAllocator(m.spec['simulation_effort_limits_nm'])
frames=[]
with (a.capture/'feedback.msgpack').open('rb') as f:
 for row in msgpack.Unpacker(f,raw=False):
  e=msgpack.unpackb(row['envelope'],raw=False)
  if e['topic']=='rt/lowstate':frames.append((e['captured'],msgpack.unpackb(e['payload'],raw=False),row['received']-e['captured']))
commands=[json.loads(x) for x in (a.capture/'commands.jsonl').read_text().splitlines()];commands=[x for x in commands if x['value'].get('ok') and x['value'].get('state')=='active'];times=np.array([x['value']['at'] for x in commands])
rows=[];errors=[]
for at,frame,age in frames[::10]:
 motors=frame['motor_state'][:29];q=np.array([x['q'] for x in motors]);dq=np.array([x['dq'] for x in motors]);imu=frame['imu_state'];R=rotation(imu['quaternion']);state=m.calculate(q,dq,imu['quaternion'],imu['gyroscope'])
 command=commands[int(np.argmin(abs(times-at)))]['value'];dt=abs(command['at']-at);c=np.array(command['command']);pd=c[:,3]*(c[:,0]-q)+c[:,4]*(c[:,1]-dq)
 try:bounded=solver.solve(state,np.zeros(29))['feedforward'].tolist()
 except InfeasibleSupport as e:bounded=None;errors.append(str(e))
 rows.append(dict(at=at,feedback_age=age,command_pair_gap_s=dt,yaw_deg=float(np.degrees(np.arctan2(R[1,0],R[0,0]))),q=q.tolist(),dq=dq.tolist(),quaternion=imu['quaternion'],gyro=imu['gyroscope'],raw_ff=state['feedforward'].tolist(),bounded_static_ff=bounded,estimated_total=(c[:,2]+pd).tolist(),device_tau_est=[x['tau_est'] for x in motors],command=c.tolist()))
def stats(key):
 arr=np.asarray([x[key] for x in rows]);return dict(mean=arr.mean(axis=0).tolist(),minimum=arr.min(axis=0).tolist(),maximum=arr.max(axis=0).tolist())
r=dict(raw_frames=len(frames),analyzed=len(rows),command_snapshots=len(commands),max_pair_gap_s=max(x['command_pair_gap_s'] for x in rows),yaw_deg=stats('yaw_deg'),raw_ff_nm=stats('raw_ff'),estimated_total_nm=stats('estimated_total'),device_tau_est_nm=stats('device_tau_est'),support_infeasible_count=len(errors),support_errors=sorted(set(errors)),control_actions=0)
(a.capture/'analysis.json').write_text(json.dumps(r,indent=2)+'\n');(a.capture/'paired-samples.json').write_text(json.dumps(rows))
print('frames',len(frames),'samples',len(rows),'infeasible',len(errors),'pair max ms',r['max_pair_gap_s']*1000)
for i in range(12):print(m.spec['joint_names'][i], 'model_ff',round(r['raw_ff_nm']['mean'][i],2),'command_total_est',round(r['estimated_total_nm']['mean'][i],2),'device_est',round(r['device_tau_est_nm']['mean'][i],2))
print('errors',r['support_errors'])
