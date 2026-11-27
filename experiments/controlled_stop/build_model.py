"""Compile the exported rigid-body model, without a simulator dependency."""
import json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent
s=json.loads((root/'model.json').read_text())
def array(v):return '{'+','.join(array(x) if isinstance(x,list) else str(float(x)) for x in v)+'}'
lines=['#include <Eigen/Dense>','#include <Eigen/Geometry>','#include <cmath>','using namespace Eigen;','using V=Vector3d; using M=Matrix3d; using J=Matrix<double,3,35>;']
lines+=['static constexpr int N=%d;'%len(s['bodies'])]
for key in ['pos','quat','ipos','iquat','inertia','axis']:
    values=[b[key] if b.get(key) is not None else [0,0,0] for b in s['bodies']]
    lines+=['static const double %s[N][%d]=%s;'%(key,len(values[0]),array(values))]
for key,values in [('parent',[b['parent'] for b in s['bodies']]),('joint',[b['joint'] if b['joint'] is not None else -1 for b in s['bodies']]),('isfree',[int(b['free']) for b in s['bodies']])]:
    lines+=['static const int %s[N]={%s};'%(key,','.join(map(str,values)))]
lines+=['static const double mass[N]=%s;'%array([b['mass'] for b in s['bodies']]),'static const int feet[2]={%s};'%','.join(map(str,s['feet']))]
lines+=[r'''
V vec(const double* v){return Map<const V>(v);}
M rot(const double* q){return Quaterniond(q[0],q[1],q[2],q[3]).normalized().toRotationMatrix();}
M skew(const V& v){M k;k<<0,-v.z(),v.y(),v.z(),0,-v.x(),-v.y(),v.x(),0;return k;}
extern "C" int compute_full(const double* q,const double* dq,const double* quat_in,const double* gyro,double* out){
 M R[N]; V p[N],w[N],alpha[N],acc[N];J jv[N],jw[N];
 for(int i=0;i<N;i++){R[i]=M::Identity();p[i]=w[i]=alpha[i]=acc[i]=V::Zero();jv[i]=jw[i]=J::Zero();}
 Matrix<double,35,1> bias=Matrix<double,35,1>::Zero();V com=V::Zero();double total=0;
 for(int i=1;i<N;i++){
  int pa=parent[i];V r=R[pa]*vec(pos[i]);p[i]=p[pa]+r;R[i]=R[pa]*rot(quat[i]);w[i]=w[pa];alpha[i]=alpha[pa];
  acc[i]=acc[pa]+alpha[pa].cross(r)+w[pa].cross(w[pa].cross(r));jv[i]=jv[pa]-skew(r)*jw[pa];jw[i]=jw[pa];
  if(isfree[i]){p[i].setZero();R[i]=rot(quat_in);w[i]=R[i]*vec(gyro);jv[i].leftCols<3>().setIdentity();jw[i].block<3,3>(0,3).setIdentity();}
  int j=joint[i];if(j>=0){V a=R[i]*vec(axis[i]);R[i]=R[i]*AngleAxisd(q[j],vec(axis[i])).toRotationMatrix();alpha[i]+=w[pa].cross(a*dq[j]);w[i]+=a*dq[j];jw[i].col(6+j)=a;}
  V offset=R[i]*vec(ipos[i]);J jc=jv[i]-skew(offset)*jw[i];
  V linear=acc[i]+alpha[i].cross(offset)+w[i].cross(w[i].cross(offset));V force=mass[i]*(linear-V(0,0,-9.81));
  M ir=R[i]*rot(iquat[i]);M inertia_world=ir*vec(inertia[i]).asDiagonal()*ir.transpose();V torque=inertia_world*alpha[i]+w[i].cross(inertia_world*w[i]);
  bias+=jc.transpose()*force+jw[i].transpose()*torque;com+=mass[i]*(p[i]+offset);total+=mass[i];
 }
 Matrix<double,12,35> jac;for(int k=0;k<2;k++){jac.block<3,35>(6*k,0)=jv[feet[k]];jac.block<3,35>(6*k+3,0)=jw[feet[k]];}
 Matrix<double,6,12> A=jac.leftCols<6>().transpose();Matrix<double,12,1> wrench=A.transpose()*(A*A.transpose()).ldlt().solve(bias.head<6>());
 Map<V> oc(out),of0(out+3),of1(out+6);oc=com/total;of0=p[feet[0]];of1=p[feet[1]];
 Map<Matrix<double,29,1>> of(out+9);of=bias.tail<29>()-jac.rightCols<29>().transpose()*wrench;
 for(int i=0;i<35;i++)out[38+i]=bias[i];
 for(int i=0;i<12;i++)for(int j=0;j<35;j++)out[73+i*35+j]=jac(i,j);
 for(int k=0;k<2;k++)for(int i=0;i<3;i++)for(int j=0;j<3;j++)out[493+k*9+i*3+j]=R[feet[k]](i,j);
 for(int i=0;i<511;i++)if(!std::isfinite(out[i]))return 1;return 0;
}
extern "C" int compute(const double* q,const double* dq,const double* quat_in,const double* gyro,double* out){
 double full[511];int result=compute_full(q,dq,quat_in,gyro,full);for(int i=0;i<38;i++)out[i]=full[i];return result;
}
''']
(root/'model_kernel.cpp').write_text('\n'.join(lines))
subprocess.run(['g++','-O2','-std=c++14','-shared','-fPIC','-I/usr/include/eigen3',str(root/'model_kernel.cpp'),'-o',str(root/'model_kernel.so')],check=True)
