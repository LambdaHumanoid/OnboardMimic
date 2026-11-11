"""Fold only TopK K derived from a provably static inferred tensor shape."""
import sys,json,numpy as np,onnx
from onnx import numpy_helper as nh,helper as h
m=onnx.load(sys.argv[1]);inferred=onnx.shape_inference.infer_shapes(m,data_prop=True)
shapes={v.name:[d.dim_value if d.HasField('dim_value') else None for d in v.type.tensor_type.shape.dim] for v in list(inferred.graph.value_info)+list(inferred.graph.input)+list(inferred.graph.output)}
nodes={o:n for n in m.graph.node for o in n.output};initializers={v.name:nh.to_array(v) for v in m.graph.initializer}
def constant(name):
 if name in initializers:return initializers[name]
 n=nodes[name]
 if n.op_type=='Constant':return nh.to_array(next(a.t for a in n.attribute if a.name=='value'))
 from onnx.reference import ReferenceEvaluator
 constants=[nh.from_array(constant(i),i) for i in n.input]
 graph=h.make_graph([n],'constant',[],[h.make_tensor_value_info(name,onnx.TensorProto.INT64,None)],constants)
 return ReferenceEvaluator(h.make_model(graph,opset_imports=[h.make_opsetid('',17)])).run(None,{})[list(n.output).index(name)]
def static_shape(name):
 dims=shapes.get(name)
 if dims is not None and all(v is not None for v in dims):return dims
 n=nodes[name]
 if n.op_type=='Expand':return list(np.broadcast_shapes(static_shape(n.input[0]),constant(n.input[1]).tolist()))
 if n.op_type=='Tile':return (np.asarray(static_shape(n.input[0]))*constant(n.input[1])).tolist()
 if n.op_type=='Cast':return static_shape(n.input[0])
 if n.op_type in ('Less','Where'):return list(np.broadcast_shapes(*(static_shape(i) for i in n.input)))
 if n.op_type=='Constant':return list(constant(name).shape)
 if n.op_type=='Reshape':
  source=static_shape(n.input[0]);target=constant(n.input[1]).tolist()
  target=[source[i] if v==0 else v for i,v in enumerate(target)]
  assert target.count(-1)<=1
  if -1 in target:target[target.index(-1)]=int(np.prod(source)//(-np.prod(target)))
  assert int(np.prod(source))==int(np.prod(target))
  return target
 raise RuntimeError('Cannot prove static shape: '+name)
report=[]
for n in m.graph.node:
 if n.op_type!='TopK':continue
 gather=nodes[n.input[1]];assert gather.op_type=='Gather'
 shape=nodes[gather.input[0]];assert shape.op_type=='Shape'
 dims=static_shape(shape.input[0]);indices=constant(gather.input[1]);assert all(x is not None for x in dims),(n.name,dims)
 k=np.asarray(dims,dtype=np.int64)[indices];name=n.name+'/onboard_static_k';m.graph.initializer.append(nh.from_array(k,name));n.input[1]=name
 report.append({'node':n.name,'shape_source':shape.input[0],'inferred_shape':dims,'k':k.tolist()})
onnx.checker.check_model(m);onnx.save(m,sys.argv[2]);print(json.dumps(report))
