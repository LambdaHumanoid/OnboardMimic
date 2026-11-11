"""Full ONNX planner comparison on CPU, no robot I/O."""
import json,time,gc
from pathlib import Path
import numpy as np,onnxruntime as ort
options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
base=Path('../planner/target_vel/V2');results=[];references=[];feeds=[]
for model_index,name in enumerate(('planner_sonic.onnx','planner_orin_compatible.onnx')):
 session=ort.InferenceSession(str(base/name),sess_options=options,providers=['CPUExecutionProvider'])
 if model_index==0:
  for mode in (0,1,4):
   feed={}
   for v in session.get_inputs():
    dtype={'tensor(float)':np.float32,'tensor(int64)':np.int64,'tensor(int32)':np.int32,'tensor(bool)':np.bool_}[v.type]
    shape=[x if isinstance(x,int) else 1 for x in v.shape];a=np.zeros(shape,dtype=dtype)
    if v.name=='context_mujoco_qpos':
     a.reshape(-1,36)[:,2]=.78874;a.reshape(-1,36)[:,3]=1;a.reshape(-1,36)[:,7:]=json.loads(Path('baseline_q.json').read_text())
    elif v.name=='mode':a.fill(mode)
    elif v.name in ('facing_direction','movement_direction'):a.flat[0]=1
    elif v.name=='target_vel':a.fill(.3 if mode==1 else 0)
    elif v.name in ('height','target_height'):a.fill(.75 if mode==4 else -1)
    elif v.name=='random_seed':a.fill(42)
    elif v.name=='allowed_pred_num_tokens':a.flat[3:6]=1
    feed[v.name]=a
   feeds.append(feed)
  print(json.dumps({'inputs':{k:{'shape':list(v.shape),'dtype':str(v.dtype)} for k,v in feeds[0].items()}}),flush=True)
 for i,feed in enumerate(feeds):
  before=time.monotonic();out=session.run(None,feed);elapsed=time.monotonic()-before
  assert all(np.isfinite(a).all() for a in out)
  if model_index==0:references.append(out)
  else:
   errors=[float(np.max(np.abs(a-b))) for a,b in zip(out,references[i])]
   for a,b in zip(out,references[i]):np.testing.assert_allclose(a,b,atol=5e-4,rtol=5e-4)
   results.append({'mode':int(feed['mode'].flat[0]),'max_abs_errors':errors,'cpu_inference_seconds':elapsed})
  print(name,i,'seconds',elapsed,flush=True)
 del session;gc.collect()
Path('planner_validation.json').write_text(json.dumps({'cases':results,'scope':'CPU ONNX original vs transformed; not TensorRT equivalence or physical validation'},indent=2));print(json.dumps(results),flush=True)
