"""Validate native SONIC cache headers before launching any controller process."""
import ctypes
import hashlib
from pathlib import Path

CACHES=(('policy/release/model_decoder.onnx','policy/release/policy_model_decoder.trt'),
        ('policy/release/model_encoder.onnx','policy/release/encoder_model_encoder.trt'),
        ('planner/target_vel/V2/planner_orin_compatible.onnx','planner/target_vel/V2/planner_planner_orin_compatible.trt'))

def digest_file(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def gpu_name():
    cuda=ctypes.CDLL('libcuda.so.1')
    if cuda.cuInit(0)!=0:raise RuntimeError('CUDA 初始化失败，未启动控制器')
    buf=ctypes.create_string_buffer(256)
    if cuda.cuDeviceGetName(buf,256,0)!=0:raise RuntimeError('无法核对 GPU，未启动控制器')
    return buf.value.decode()

def validate_caches(deploy, gpu=None):
    deploy=Path(deploy)
    for model,cache in CACHES:
        for name in (model,cache):
            if not (deploy/name).is_file():raise RuntimeError('缺少推理模型或缓存：'+name+'；未启动控制器，请修复发布制品')
        if (deploy/cache).stat().st_size<=64:raise RuntimeError('推理缓存为空或截断：'+cache)
    gpu=gpu if gpu is not None else gpu_name()
    for model,cache in CACHES:
        digest=digest_file(deploy/model)
        digest=hashlib.sha256((digest+gpu).encode()).hexdigest()
        expected=hashlib.sha256((digest+'0').encode()).hexdigest().encode()
        with (deploy/cache).open('rb') as stream:actual=stream.read(64)
        if actual!=expected:raise RuntimeError('推理缓存与模型、GPU 或精度不匹配：'+cache+'；未启动控制器')
    return {'validated':True,'gpu':gpu,'cache_count':len(CACHES)}

if __name__=='__main__':
    import sys,os,json
    result=validate_caches(sys.argv[1])
    print('Runtime assets verified: '+json.dumps(result),flush=True)
    if len(sys.argv)>2:os.execv(sys.argv[2],sys.argv[2:])
