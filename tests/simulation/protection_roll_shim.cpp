// Offline adapter only: no ROS, socket, process signaling or hardware access.
#include "protection_roll.hpp"
using namespace sonic_bridge;
// Injection exists only in this transport-free simulation adapter.
static uint64_t failed_mask=0;
static int fault_kind=0;
static double fault_at=.02;
extern "C" {
void protection_faults(uint64_t mask,int kind,double at){failed_mask=mask;fault_kind=kind;fault_at=at;}
void* protection_create(){return new ProtectedWriter;}
void protection_tune(void* p,double bias){static_cast<ProtectedWriter*>(p)->roll.ankle_bias=bias;}
void protection_destroy(void* p){delete static_cast<ProtectedWriter*>(p);}
double protection_bias(void* p){return static_cast<ProtectedWriter*>(p)->roll.ankle_bias;}
const char* protection_strategy(void* p){return static_cast<ProtectedWriter*>(p)->roll.strategy;}
double protection_entry_speed(void* p){return static_cast<ProtectedWriter*>(p)->roll.entry_max_speed;}
double protection_foot_difference(void* p){return static_cast<ProtectedWriter*>(p)->roll.foot_height_difference;}
int protection_step(void* p,double now,double captured,const float* q,const float* dq,double roll,double pitch,const float* source,int action,float* output){
 auto& w=*static_cast<ProtectedWriter*>(p);
 RollFeedback f;f.valid=true;f.captured=captured;f.roll=roll;f.pitch=pitch;
 for(int i=0;i<29;++i){f.q[i]=q[i];f.dq[i]=dq[i];}
 if(fault_kind&&now>=fault_at){
#ifdef OM_FAULT_BASELINE
  // Old receiver discarded motor-fault/nonfinite feedback as an invalid frame.
  f.valid=false;
#else
  for(size_t i=0;i<29;++i)if(failed_mask&(uint64_t(1)<<i)){
   if(fault_kind==1){f.motor_faults[i]=0x200;f.fault=true;}
   else{f.invalid_joints[i]=true;f.q[i]=std::numeric_limits<float>::quiet_NaN();f.dq[i]=std::numeric_limits<float>::quiet_NaN();}
  }
#endif
 }
 if(action==1){
  auto bias=w.roll.ankle_bias;w.reset(1);w.roll.ankle_bias=bias;Packet packet;packet.stream=42;packet.seq=1;packet.send_ns=uint64_t(now*1e9);
  for(int i=0;i<29;++i)packet.cmd.motor[i]={1,source[i*5],source[i*5+1],source[i*5+2],source[i*5+3],source[i*5+4],0};
  w.observe(packet,now,f);w.arm(1,42,f,now);
 }
 if(action==3)w.cancel(1);
 w.advance(f,now,action==2);
 const auto& c=w.output();
 for(int i=0;i<29;++i){const auto& m=c.motor[i];output[5*i]=m.q;output[5*i+1]=m.dq;output[5*i+2]=m.tau;output[5*i+3]=m.kp;output[5*i+4]=m.kd;}
 return int(w.roll.phase);
}
double protection_brake_events(void* p){return static_cast<ProtectedWriter*>(p)->roll.speed_brake_events;}
const char* protection_reason(void* p){return static_cast<ProtectedWriter*>(p)->roll.reason;}
}
