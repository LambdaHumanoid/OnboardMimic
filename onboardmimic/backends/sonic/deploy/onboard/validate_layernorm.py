"""Compare all rewritten LayerNorm blocks to ONNX reference semantics."""
import json,sys
from pathlib import Path
import numpy as np,onnx
from onnx import helper as h,numpy_helper as nh,TensorProto as T
from onnx.reference import ReferenceEvaluator
m=onnx.load(sys.argv[1]);converted=onnx.load(sys.argv[2]);initializers={x.name:x for x in m.graph.initializer};new_initializers={x.name:x for x in converted.graph.initializer};rng=np.random.default_rng(42);errors=[];count=0
for n in m.graph.node:
 if n.op_type!='LayerNormalization':continue
 w=initializers[n.input[1]];b=initializers[n.input[2]];width=int(np.prod(w.dims));shape=[2,3,width]
 inputs=[h.make_tensor_value_info(n.input[0],T.FLOAT,shape)];outputs=[h.make_tensor_value_info(n.output[0],T.FLOAT,shape)]
 ref=h.make_model(h.make_graph([n],'reference',inputs,outputs,[w,b]),opset_imports=[h.make_opsetid('',17)])
 prefix=n.name+'/onboard_decomposed';nodes=[x for x in converted.graph.node if x.name.startswith(prefix+'/')]
 target=h.make_model(h.make_graph(nodes,'converted',inputs,outputs,[w,b,new_initializers[prefix+'/eps']]),opset_imports=[h.make_opsetid('',17)])
 a=ReferenceEvaluator(ref);c=ReferenceEvaluator(target)
 for x in (rng.normal(size=shape).astype('float32'),np.ones(shape,dtype='float32'),(1+1e-3*rng.normal(size=shape)).astype('float32')):
  y=a.run(None,{n.input[0]:x})[0];z=c.run(None,{n.input[0]:x})[0]
  error=float(np.max(np.abs(y-z)));errors.append(error);np.testing.assert_allclose(z,y,atol=2e-5,rtol=2e-5)
 count+=1
report={'layernorm_nodes':count,'cases':len(errors),'max_abs_error':max(errors),'scope':'individual FP32 LayerNorm blocks; not full-model or physical equivalence'}
Path('layernorm_validation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
