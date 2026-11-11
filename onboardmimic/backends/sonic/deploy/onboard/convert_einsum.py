"""Lower the planner's batched transpose-product Einsum to Transpose+MatMul."""
import sys,json,numpy as np,onnx
from onnx import helper as h
m=onnx.load(sys.argv[1]);inferred=onnx.shape_inference.infer_shapes(m)
ranks={v.name:len(v.type.tensor_type.shape.dim) for v in list(inferred.graph.value_info)+list(inferred.graph.input)}
nodes=[];report=[]
for n in m.graph.node:
 if n.op_type!='Einsum':nodes.append(n);continue
 equation=next(a.s.decode() for a in n.attribute if a.name=='equation').replace(' ','')
 assert equation=='...xnm,...xno->...xmo',equation
 rank=ranks[n.input[0]];assert rank>=3
 perm=list(range(rank));perm[-2:]=perm[-2:][::-1];temp=n.name+'/onboard_transposed'
 nodes.extend([h.make_node('Transpose',[n.input[0]],[temp],perm=perm,name=temp),h.make_node('MatMul',[temp,n.input[1]],list(n.output),name=n.name+'/onboard_matmul')])
 rng=np.random.default_rng(42);a=rng.normal(size=(2,3,5,7));b=rng.normal(size=(2,3,5,4));np.testing.assert_allclose(np.einsum(equation,a,b),a.swapaxes(-1,-2)@b,atol=1e-12)
 report.append({'node':n.name,'rank':rank,'permutation':perm,'equation':equation})
m.graph.ClearField('node');m.graph.node.extend(nodes);onnx.checker.check_model(m);onnx.save(m,sys.argv[2]);print(json.dumps(report))
