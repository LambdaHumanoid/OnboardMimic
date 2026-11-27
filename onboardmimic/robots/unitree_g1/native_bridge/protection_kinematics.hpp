#pragma once
// Leg transforms from experiments/controlled_stop/model.json (sha256 d4f294f5c8c38e6482b62044d5faa452c3ecb172f081bd2e83b9b2fc475d8cdd).
// Relative ankle heights are a kinematic hint, NOT measured foot contact.
#include <array>
#include <cmath>
namespace sonic_bridge { namespace protection_kinematics {
using Matrix=std::array<double,9>;
inline Matrix multiply(const Matrix&a,const Matrix&b){Matrix c{};for(int i=0;i<3;++i)for(int j=0;j<3;++j)for(int k=0;k<3;++k)c[i*3+j]+=a[i*3+k]*b[k*3+j];return c;}
inline Matrix quaternion(double w,double x,double y,double z){return {1-2*(y*y+z*z),2*(x*y-w*z),2*(x*z+w*y),2*(x*y+w*z),1-2*(x*x+z*z),2*(y*z-w*x),2*(x*z-w*y),2*(y*z+w*x),1-2*(x*x+y*y)};}
inline constexpr double pos[12][3]={{0.0,0.064452,-0.1027},{0.0,0.052,-0.030465},{0.025001,0.0,-0.12412},{-0.078273,0.0021489,-0.17734},{0.0,-9.4445e-05,-0.30001},{0.0,0.0,-0.017558},{0.0,-0.064452,-0.1027},{0.0,-0.052,-0.030465},{0.025001,0.0,-0.12412},{-0.078273,-0.0021489,-0.17734},{0.0,9.4445e-05,-0.30001},{0.0,0.0,-0.017558}};
inline constexpr double quat[12][4]={{1.0,0.0,0.0,0.0},{0.996178685660368,0.0,-0.08733857244071258,0.0},{1.0,0.0,0.0,0.0},{0.996178685660368,0.0,0.08733857244071258,0.0},{1.0,0.0,0.0,0.0},{1.0,0.0,0.0,0.0},{1.0,0.0,0.0,0.0},{0.996178685660368,0.0,-0.08733857244071258,0.0},{1.0,0.0,0.0,0.0},{0.996178685660368,0.0,0.08733857244071258,0.0},{1.0,0.0,0.0,0.0},{1.0,0.0,0.0,0.0}};
inline constexpr double axis[12][3]={{0.0,1.0,0.0},{1.0,0.0,0.0},{0.0,0.0,1.0},{0.0,1.0,0.0},{0.0,1.0,0.0},{1.0,0.0,0.0},{0.0,1.0,0.0},{1.0,0.0,0.0},{0.0,0.0,1.0},{0.0,1.0,0.0},{0.0,1.0,0.0},{1.0,0.0,0.0}};
inline double foot_height(const std::array<float,35>&q,double roll,double pitch,int side){
 auto R=multiply(quaternion(std::cos(pitch/2),0,std::sin(pitch/2),0),quaternion(std::cos(roll/2),std::sin(roll/2),0,0));double z=0;
 for(int j=side*6;j<side*6+6;++j){z+=R[6]*pos[j][0]+R[7]*pos[j][1]+R[8]*pos[j][2];R=multiply(R,quaternion(quat[j][0],quat[j][1],quat[j][2],quat[j][3]));double h=q[j]/2.;R=multiply(R,quaternion(std::cos(h),axis[j][0]*std::sin(h),axis[j][1]*std::sin(h),axis[j][2]*std::sin(h)));}
 return z;
}
}}
