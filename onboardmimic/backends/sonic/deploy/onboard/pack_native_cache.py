"""Wrap local trtexec plans in the existing SONIC cache header format."""
from pathlib import Path
import ctypes,hashlib,json
validation=Path("planner_engine_validation.json")
if not validation.exists() or not json.loads(validation.read_text()).get("passed"):
 raise SystemExit("Refusing to pack planner cache: numerical validation has not passed")
policy_validation=Path('engine_validation.json')
if not policy_validation.exists() or not all(x.get('passed') for x in json.loads(policy_validation.read_text())):
 raise SystemExit('Refusing to pack cache: policy engine numerical validation has not passed')
cuda=ctypes.CDLL('libcuda.so.1');assert cuda.cuInit(0)==0
buf=ctypes.create_string_buffer(256);assert cuda.cuDeviceGetName(buf,256,0)==0
gpu=buf.value.decode();root=Path('..');manifest=[]
for onnx_name,engine_name,prefix in [('policy/release/model_encoder.onnx','encoder_fp32','encoder_'),('policy/release/model_decoder.onnx','decoder_fp32','policy_'),('planner/target_vel/V2/planner_orin_compatible.onnx','planner_fp32','planner_')]:
 src=root/onnx_name;engine=Path('engines')/(engine_name+'.engine');assert engine.exists(),engine
 digest=hashlib.sha256(src.read_bytes()).hexdigest();digest=hashlib.sha256((digest+gpu).encode()).hexdigest();digest=hashlib.sha256((digest+'0').encode()).hexdigest()
 target=src.with_name(prefix+src.stem+'.trt')
 with target.open('wb') as f:
  f.write(digest.encode())
  with engine.open('rb') as e:
   for chunk in iter(lambda:e.read(1024*1024),b''):f.write(chunk)
 manifest.append({'model':onnx_name,'gpu':gpu,'precision':'FP32','cache':str(target),'cache_key':digest})
Path('native_cache_manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest,indent=2))
