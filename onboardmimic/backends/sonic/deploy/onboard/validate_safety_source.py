"""Check the staged real-only controller gate before any future integration."""
from pathlib import Path
import json
import re
root=Path('..')
s=(root/'src/g1/g1_deploy_onnx_ref/src/g1_deploy_onnx_ref.cpp').read_text()
assignments=re.findall(r'\bsimulation_mode_\s*=\s*([^;]+);',s)
assert assignments and all(v.strip()=='false' for v in assignments),assignments
assert 'ONBOARD_ALWAYS_REAL_SAFETY' in s
trt=(root/'src/TRTInference/InferenceEngine.cpp').read_text()
assert 'config->clearFlag(nvinfer1::BuilderFlag::kTF32);' in trt
print(json.dumps({'real_safety_for_loopback':True,'tf32_rebuild_disabled':True,'scope':'static configuration checks; no robot motion'}))
inc=root/'src/g1/g1_deploy_onnx_ref/include'
for name,priority in (('encoder.hpp','true'),('control_policy.hpp','true'),('localmotion_kplanner_tensorrt.hpp','false')):
 text=(inc/name).read_text()
 assert 'CreateOnboardCudaStream(&cuda_stream_, '+priority+')' in text,name
print(json.dumps({'policy_high_planner_low_streams':True,'scope':'static configuration, no motor output'}))
