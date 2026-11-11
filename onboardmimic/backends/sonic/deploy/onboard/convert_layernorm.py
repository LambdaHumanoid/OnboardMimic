"""Expand ONNX LayerNormalization into FP32 primitives for TensorRT 8.5.
Writes a separate model; never changes the original or drops normalizations.
"""
import sys,json
from pathlib import Path
import onnx
from onnx import helper as h,TensorProto as T
m=onnx.load(sys.argv[1]);count=0
# Model uses opset 17 where ReduceMean axes are attributes.
assert next(x.version for x in m.opset_import if x.domain=='')==17
nodes=[]
for n in m.graph.node:
 if n.op_type!='LayerNormalization':nodes.append(n);continue
 attrs={a.name:h.get_attribute_value(a) for a in n.attribute}
 if attrs.get('axis',-1)!=-1 or attrs.get('stash_type',1)!=1:raise RuntimeError('Unsupported LayerNorm contract')
 if any(n.output[1:]):raise RuntimeError('Unexpected extra LayerNorm outputs')
 prefix=n.name+'/onboard_decomposed';x,w,b=n.input;eps=prefix+'/eps'
 m.graph.initializer.append(h.make_tensor(eps,T.FLOAT,[],[attrs.get('epsilon',1e-5)]))
 def add(op,ins,label,**kw):
  out=prefix+'/'+label;nodes.append(h.make_node(op,ins,[out],name=out,**kw));return out
 mean=add('ReduceMean',[x],'mean',axes=[-1],keepdims=1)
 centered=add('Sub',[x,mean],'centered');sq=add('Mul',[centered,centered],'square')
 var=add('ReduceMean',[sq],'variance',axes=[-1],keepdims=1)
 root=add('Sqrt',[add('Add',[var,eps],'variance_eps')],'std')
 normalized=add('Div',[centered,root],'normalized');scaled=add('Mul',[normalized,w],'scaled')
 nodes.append(h.make_node('Add',[scaled,b],list(n.output[:1]),name=prefix+'/result'));count+=1
m.graph.ClearField('node');m.graph.node.extend(nodes)
# Remove tracing doc strings only, which carry no execution semantics.
for n in m.graph.node:n.doc_string=''
onnx.checker.check_model(m)
onnx.save(m,sys.argv[2]);print(json.dumps({'expanded_layernorm_nodes':count,'output':sys.argv[2]}))
