"""Compare native TensorRT planner against unchanged ONNX on CPU."""
import json,time,sys
from pathlib import Path
import numpy as np,onnxruntime as ort
from benchmark import Model
engine_path=sys.argv[1] if len(sys.argv)>1 else 'engines/planner_fp32.engine'
report_path=sys.argv[2] if len(sys.argv)>2 else 'planner_engine_validation.json'
model=Model(engine_path)
options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
session=ort.InferenceSession('../planner/target_vel/V2/planner_sonic.onnx',sess_options=options,providers=['CPUExecutionProvider'])
results=[];passed=True
extended='--extended' in sys.argv[3:]
baseline=model.arrays['context_mujoco_qpos'].copy()
cases=[(mode,seed,perturb) for mode in (0,1,4) for seed in ((0,42,1234,65535) if extended else (42,)) for perturb in ((False,True) if extended else (False,))]
for mode,seed,perturb in cases:
 model.arrays['random_seed'].fill(seed)
 model.arrays['context_mujoco_qpos'][:]=baseline
 model.arrays['facing_direction'][:]=[1,0,0]
 if perturb:
  rng=np.random.default_rng(seed)
  model.arrays['context_mujoco_qpos'].reshape(-1,36)[:,7:]+=rng.uniform(-.01,.01,(4,29)).astype('float32')
  model.arrays['facing_direction'][:]=[np.cos(.3),np.sin(.3),0]
 model.arrays['mode'].fill(mode);model.arrays['target_vel'].fill(.3 if mode==1 else 0);model.arrays['height'].fill(.75 if mode==4 else -1);model.arrays['movement_direction'].flat[0]=1
 feed={}
 for v in session.get_inputs():
  dtype={'tensor(float)':np.float32,'tensor(int64)':np.int64,'tensor(int32)':np.int32,'tensor(bool)':np.bool_}[v.type]
  feed[v.name]=model.arrays[v.name].astype(dtype)
 reference=session.run(model.outputs,feed);before=time.monotonic();model.run();elapsed=time.monotonic()-before
 errors={k:float(np.max(np.abs(model.arrays[k]-a))) for k,a in zip(model.outputs,reference)}
 ok=all(np.allclose(model.arrays[k],a,atol=1e-3,rtol=1e-3) for k,a in zip(model.outputs,reference));passed=passed and ok
 results.append({'mode':mode,'seed':seed,'perturbed_context':perturb,'max_abs_errors':errors,'within_smoke_tolerance':ok,'inference_ms':elapsed*1000})
report={'engine':engine_path,'cases':results,'passed':passed,'scope':'24 synthetic cases across modes/seeds/small posture perturbations; not closed-loop or physical validation' if extended else 'three deterministic synthetic planner cases only; not physical validation'}
Path(report_path).write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if not passed:raise SystemExit(1)
