#include "protection_feedback.hpp"
#include "protection_control.hpp"
#include <cassert>
#include <iostream>
#include <limits>
using namespace sonic_bridge;
RollFeedback feedback(double t){
 RollFeedback f;f.valid=true;f.captured=t;
 for(size_t i=0;i<29;++i)f.q[i]=std::clamp(0.,roll_parameters::qmin[i],roll_parameters::qmax[i]);
 f.q[3]=f.q[9]=.67;f.q[0]=f.q[6]=-.31;f.q[4]=f.q[10]=-.36;return f;
}
Packet packet(double t,uint64_t seq=1){Packet p;p.stream=42;p.seq=seq;p.send_ns=uint64_t(t*1e9);auto f=feedback(t);for(size_t i=0;i<29;++i)p.cmd.motor[i]={1,f.q[i],0,0,40,3,0};p.cmd.crc=crc(p.cmd);return p;}
ProtectedWriter armed(){ProtectedWriter w;assert(w.reset(1));auto f=feedback(1);assert(w.observe(packet(1),1,f));assert(w.arm(1,42,f,1));return w;}
void zero_motor(const Command& c,size_t j){auto m=c.motor[j];assert(m.mode==0&&m.q==0&&m.dq==0&&m.tau==0&&m.kp==0&&m.kd==0);assert(c.crc==crc(c));}
RollFeedback invalid(size_t j,double t,bool explicit_flag){auto f=feedback(t);if(explicit_flag){f.invalid_joints[j]=true;f.q[j]=123456;f.dq[j]=-99999;}else{f.q[j]=std::numeric_limits<float>::quiet_NaN();f.dq[j]=std::numeric_limits<float>::infinity();}return f;}
msgpack::object_handle encoded(size_t joint,bool invalid_q,bool invalid_dq,uint32_t fault=0,float roll=0){
 msgpack::sbuffer b;msgpack::packer<msgpack::sbuffer> p(b);p.pack_map(2);p.pack("motor_state");p.pack_array(35);
 for(size_t i=0;i<35;++i){p.pack_map(3);p.pack("q");p.pack(i==joint&&invalid_q?std::numeric_limits<float>::quiet_NaN():0.f);p.pack("dq");p.pack(i==joint&&invalid_dq?std::numeric_limits<float>::infinity():0.f);p.pack("motorstate");p.pack(i==joint?fault:0);}
 p.pack("imu_state");p.pack_map(1);p.pack("rpy");p.pack(std::vector<float>{roll,0,0});return msgpack::unpack(b.data(),b.size());
}
int main(){
 for(size_t j=0;j<29;++j){
  assert(fault_profiles::standing_fold[j]);
  // Actual production decoder: preserve healthy data, identify bad joint.
  for(int mode=0;mode<3;++mode){auto wire=encoded(j,mode==0,mode==1,mode==2?0x200:0);auto f=decode_protection_feedback(wire.get(),1,1,true);assert(f.valid);assert(f.motor_faults[j]==(mode==2?0x200u:0));assert(f.invalid_joints[j]==(mode!=2));for(size_t k=0;k<29;++k)if(k!=j)assert(!f.invalid_joints[k]&&!f.motor_faults[k]);}
  // Complete all-joint sensor-loss trajectories and invalid-value independence.
  auto a=armed(),b=armed();
  for(int n=1;n<3600;++n){double t=1+n*.002;auto fa=invalid(j,t,false),fb=invalid(j,t,true);a.advance(fa,t);b.advance(fb,t);auto ca=a.output(),cb=b.output();zero_motor(ca,j);zero_motor(cb,j);assert(ca.crc==cb.crc);for(size_t k=0;k<29;++k){assert(std::isfinite(ca.motor[k].q));assert(std::isfinite(ca.motor[k].kd));assert(ca.motor[k].q==cb.motor[k].q&&ca.motor[k].kp==cb.motor[k].kp&&ca.motor[k].kd==cb.motor[k].kd);}}
  assert(a.roll.phase==RollPhase::finished&&b.roll.phase==RollPhase::finished);
  // Single known motor fault, cleared bit stays latched; new faults downgrade.
  auto w=armed();auto f=feedback(1.002);f.motor_faults[j]=0x200;f.fault=true;w.advance(f,1.002,true);assert(w.roll.phase==RollPhase::running);zero_motor(w.output(),j);
  f=feedback(1.004);w.advance(f,1.004);zero_motor(w.output(),j);
  auto other=(j+1)%29;f=feedback(1.006);f.motor_faults[other]=0x200;w.advance(f,1.006);assert(w.roll.phase==RollPhase::aborted);zero_motor(w.output(),j);zero_motor(w.output(),other);
  assert(!w.observe(packet(1.008,2),1.008,feedback(1.008)));
  w.cancel(1);zero_motor(w.output(),j);assert(w.reset(2));
  f=invalid(j,1.01,false);assert(!w.arm(2,0,f,1.01));assert(w.disabled_faults[j]);
  assert(!w.arm(2,999,feedback(1.011),1.011));assert(w.disabled_faults[j]);zero_motor(w.output(),j);
  assert(w.arm(2,0,feedback(1.012),1.012));assert(!w.disabled_faults[j]&&!w.disabled_feedback[j]);
  auto loss=armed();loss.advance(invalid(j,1.002,false),1.002);loss.advance(feedback(1.004),1.004);zero_motor(loss.output(),j);assert(loss.disabled_feedback[j]);loss.cancel(1);zero_motor(loss.output(),j);
 }
 for(uint32_t code:{1u,0x201u,0x400u}){auto w=armed();auto f=feedback(1.002);f.motor_faults[1]=code;f.fault=true;w.advance(f,1.002,true);assert(w.roll.phase==RollPhase::aborted);zero_motor(w.output(),1);}
 for(int mode=0;mode<5;++mode){auto w=armed();auto f=invalid(1,1.002,false);if(mode==0)f.invalid_joints[7]=true;if(mode==1)f.roll=std::numeric_limits<double>::quiet_NaN();if(mode==2)f.captured=.9;if(mode==3)f.dq[3]=2.01;if(mode==4)f.pitch=.251;w.advance(f,1.002,true);assert(w.roll.phase==RollPhase::aborted);}
 for(int mode=0;mode<3;++mode){auto wire=encoded(mode==0?30:1,true,false,0,mode==1?std::numeric_limits<float>::infinity():0);bool rejected=false;try{decode_protection_feedback(wire.get(),1,mode==2?1.04:1,true);}catch(...){rejected=true;}assert(rejected);}
 {auto wire=encoded(1,true,false);bool rejected=false;try{decode_protection_feedback(wire.get(),1,1,false);}catch(...){rejected=true;}assert(rejected);}
 std::cout<<"29 joint faults, 29 feedback losses, value independence, latch/reset/estop/decoder/CRC checks passed\n";
}
