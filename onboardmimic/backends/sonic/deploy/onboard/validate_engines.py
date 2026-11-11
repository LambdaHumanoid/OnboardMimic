"""Offline numerical smoke test: native ONNX CPU vs Orin TensorRT."""
import json
from pathlib import Path
import numpy as np,onnxruntime as ort
from benchmark import Model
options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
rng=np.random.default_rng(42);results=[]
for name in ('encoder','decoder'):
 model=Model('engines/'+name+'_fp32.engine')
 session=ort.InferenceSession('../policy/release/model_'+name+'.onnx',sess_options=options,providers=['CPUExecutionProvider'])
 errors=[];passed=True
 for i in range(3):
  for k in model.inputs:model.arrays[k][:]=rng.normal(0,.01,model.arrays[k].shape).astype(model.arrays[k].dtype) if i else 0
  if name=='encoder':model.arrays[model.inputs[0]].ravel()[:4]=[1,0,0,0]
  ref=session.run(model.outputs,{k:model.arrays[k].copy() for k in model.inputs});model.run()
  for k,a in zip(model.outputs,ref):
   b=model.arrays[k];assert np.isfinite(a).all() and np.isfinite(b).all()
   errors.append(float(np.max(np.abs(a-b))))
   passed = passed and bool(np.allclose(a,b,atol=1e-3,rtol=1e-3))
 results.append({'model':name,'passed':passed,'synthetic_cases':3,'max_abs_error':max(errors),'scope':'synthetic numerical smoke test only'})
 del session,model
Path('engine_validation.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))

if not all(x["passed"] for x in results):raise SystemExit("Policy engine numerical validation failed")
