"""Model-only Orin benchmark. No sockets, DDS, bridge or motor output.
Synthetic fixed observations: latency evidence, not balance validation.
"""
import ctypes as C,json,time,threading,sys
from pathlib import Path
import numpy as np
np.bool = np.bool_  # TensorRT 8.5 Python bindings use the removed NumPy alias.
import tensorrt as trt
lib=C.CDLL('/usr/local/cuda/lib64/libcudart.so')
P=C.c_void_p;I=C.c_int;S=C.c_size_t
signatures={'cudaSetDevice':[I],'cudaMalloc':[C.POINTER(P),S],'cudaMallocHost':[C.POINTER(P),S],'cudaStreamCreate':[C.POINTER(P)],'cudaMemcpyAsync':[P,P,S,I,P],'cudaStreamSynchronize':[P],'cudaStreamBeginCapture':[P,I],'cudaStreamEndCapture':[P,C.POINTER(P)],'cudaGraphInstantiate':[C.POINTER(P),P,P,P,S],'cudaGraphLaunch':[P,P]}
for n,args in signatures.items():getattr(lib,n).argtypes=args;getattr(lib,n).restype=I
def cuda(name,*args):
 code=getattr(lib,name)(*args)
 if code:raise RuntimeError('%s: CUDA %s'%(name,code))
logger=trt.Logger(trt.Logger.WARNING)
class Model:
 def __init__(self,path):
  self.runtime=trt.Runtime(logger);self.engine=self.runtime.deserialize_cuda_engine(Path(path).read_bytes())
  if self.engine is None:raise RuntimeError('Deserialize failed: '+str(path))
  self.context=self.engine.create_execution_context();self.stream=P();cuda('cudaStreamCreate',C.byref(self.stream));self.bindings=[];self.arrays={};self.device={};self.inputs=[];self.outputs=[]
  for i in range(self.engine.num_bindings):
   name=self.engine.get_binding_name(i);shape=tuple(self.context.get_binding_shape(i));dtype=np.dtype(trt.nptype(self.engine.get_binding_dtype(i)))
   assert all(v>=0 for v in shape),(name,shape)
   size=int(np.prod(shape))*dtype.itemsize;host=P();device=P();cuda('cudaMallocHost',C.byref(host),size);cuda('cudaMalloc',C.byref(device),size)
   raw=(C.c_ubyte*size).from_address(host.value);array=np.frombuffer(raw,dtype=dtype).reshape(shape);array.fill(0)
   self.arrays[name]=array;self.device[name]=device;self.bindings.append(device.value)
   (self.inputs if self.engine.binding_is_input(i) else self.outputs).append(name)
  for name in self.inputs:
   a=self.arrays[name]
   if name=='context_mujoco_qpos':
    a.reshape(-1,36)[:,2]=.78874;a.reshape(-1,36)[:,3]=1
    a.reshape(-1,36)[:,7:]=np.array(json.loads(Path('baseline_q.json').read_text()))
   elif name in ('facing_direction','movement_direction'):a.flat[0]=1
   elif name in ('height','target_height'):a.fill(-1)
   elif name=='allowed_pred_num_tokens':a.flat[3:6]=1
   elif name=='random_seed':a.fill(42)
  if 'encoder' in str(path):self.arrays[self.inputs[0]].ravel()[:4]=[1,0,0,0]
  self.graph_exec=None
  self.run()
  graph=P();executable=P();cuda('cudaStreamBeginCapture',self.stream,2)
  if not self.context.execute_async_v2(self.bindings,self.stream.value):raise RuntimeError('Graph enqueue failed')
  cuda('cudaStreamEndCapture',self.stream,C.byref(graph));cuda('cudaGraphInstantiate',C.byref(executable),graph,None,None,0);self.graph_exec=executable
  print(json.dumps({'model':str(path),'inputs':{k:{'shape':list(self.arrays[k].shape),'dtype':str(self.arrays[k].dtype)} for k in self.inputs}}),flush=True)
 def run(self):
  for k in self.inputs:
   a=self.arrays[k];cuda('cudaMemcpyAsync',self.device[k],P(a.ctypes.data),a.nbytes,1,self.stream)
  if self.graph_exec:cuda('cudaGraphLaunch',self.graph_exec,self.stream)
  elif not self.context.execute_async_v2(self.bindings,self.stream.value):raise RuntimeError('Inference failed')
  for k in self.outputs:
   a=self.arrays[k];cuda('cudaMemcpyAsync',P(a.ctypes.data),self.device[k],a.nbytes,2,self.stream)
  cuda('cudaStreamSynchronize',self.stream)
  if any(not np.isfinite(self.arrays[k]).all() for k in self.outputs):raise RuntimeError('Nonfinite output')

def stats(values,budget):
 a=np.asarray(values);return {'n':len(a),'mean_ms':float(a.mean()),'p99_ms':float(np.percentile(a,99)),'max_ms':float(a.max()),'over_budget':int((a>budget).sum()),'budget_ms':budget}
def main():
 cuda('cudaSetDevice',0)
 enc=Model('engines/encoder_fp32.engine');dec=Model('engines/decoder_fp32.engine')
 planner=Model('engines/planner_fp32.engine') if Path('engines/planner_fp32.engine').exists() else None
 for _ in range(10):enc.run();dec.run()
 if planner:
  for _ in range(3):planner.run()
 duration=float(sys.argv[1]) if len(sys.argv)>1 else 30.;results={};errors=[];start=time.monotonic()+.2
 def worker(name,period,fn):
  try:
   cuda('cudaSetDevice',0);values=[];lateness=[];tick=start
   while tick<start+duration:
    time.sleep(max(0,tick-time.monotonic()));beg=time.monotonic();fn();end=time.monotonic();values.append((end-beg)*1000);lateness.append((end-tick)*1000);tick+=period
   results[name]={'work':stats(values,period*1000),'completion_from_deadline':stats(lateness,period*1000)}
  except Exception as e:errors.append(name+': '+str(e))
 def policy():
  enc.run()
  token=enc.arrays[enc.outputs[0]].ravel();observation=dec.arrays[dec.inputs[0]].ravel()
  assert token.size==64 and observation.size>=64
  observation[:64]=token;dec.run()
 threads=[threading.Thread(target=worker,args=('encoder_decoder',.02,policy))]
 if planner:threads.append(threading.Thread(target=worker,args=('planner',.1,planner.run)))
 for t in threads:t.start()
 for t in threads:t.join()
 report={'duration_s':duration,'synthetic_observations':True,'motor_output':False,'planner_present':bool(planner),'results':results,'errors':errors}
 Path('benchmark_result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2),flush=True)
 if errors:raise SystemExit(1)
if __name__=='__main__':main()
