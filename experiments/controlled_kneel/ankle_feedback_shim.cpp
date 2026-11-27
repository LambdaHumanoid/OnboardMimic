// Offline experiment only. No ROS, transport, motor writer or simulator oracle.
// Estimate pitch rate from successive timestamped IMU samples, then choose the
// ankle pulse once at takeover. Missing history conservatively retains baseline.
#include "protection_roll.hpp"
#include "ankle_feedback_parameters.hpp"
using namespace sonic_bridge;
struct Experiment {
 ProtectedWriter writer;
 double previous_time=-1,previous_pitch=0,rate=0;
 int samples=0;
 void measure(double time,double pitch){
  if(!std::isfinite(time)||!std::isfinite(pitch))return;
  if(previous_time>=0&&time>previous_time&&time-previous_time<=.03){
   double dt=time-previous_time;
   double velocity=std::remainder(pitch-previous_pitch,2*3.141592653589793)/dt;
   rate+=(1-std::exp(-dt/.02))*(velocity-rate);++samples;
  }else if(previous_time<0||time-previous_time>.03){samples=0;rate=0;}
  if(time>previous_time){previous_time=time;previous_pitch=pitch;}
 }
};
extern "C" {
void* protection_create(){return new Experiment;}
void protection_tune(void* p,double bias){static_cast<Experiment*>(p)->writer.roll.ankle_bias=bias;}
void protection_destroy(void* p){delete static_cast<Experiment*>(p);}
double protection_bias(void* p){return static_cast<Experiment*>(p)->writer.roll.ankle_bias;}
int protection_step(void* p,double now,double captured,const float* q,const float* dq,double roll,double pitch,const float* source,int action,float* output){
 auto& e=*static_cast<Experiment*>(p);auto& w=e.writer;
 RollFeedback f;f.valid=true;f.captured=captured;f.roll=roll;f.pitch=pitch;
 for(int i=0;i<29;++i){f.q[i]=q[i];f.dq[i]=dq[i];}
 if(action==1){
  auto bias=w.roll.ankle_bias;w.reset(1);w.roll.ankle_bias=bias;
  e.previous_time=-1;e.samples=0;e.rate=0;
  Packet packet;packet.stream=42;packet.seq=1;packet.send_ns=uint64_t(now*1e9);
  for(int i=0;i<29;++i)packet.cmd.motor[i]={1,source[i*5],source[i*5+1],source[i*5+2],source[i*5+3],source[i*5+4],0};
  w.observe(packet,now,f);w.arm(1,42,f,now);
 }
 e.measure(captured,pitch);
 if(action==3)w.cancel(1);
 if(w.armed&&!w.fenced&&(action==2||now-w.wbc.last_valid>w.timeout)&&ProtectionRoll::fresh(f,now)&&e.samples>=3){
  w.roll.ankle_bias=std::clamp(ankle_feedback::base+ankle_feedback::rate_gain*e.rate+ankle_feedback::pitch_gain*pitch,0.,ankle_feedback::maximum);
 }
 w.advance(f,now,action==2);
 const auto& c=w.output();
 for(int i=0;i<29;++i){const auto& m=c.motor[i];output[5*i]=m.q;output[5*i+1]=m.dq;output[5*i+2]=m.tau;output[5*i+3]=m.kp;output[5*i+4]=m.kd;}
 return int(w.roll.phase);
}
const char* protection_reason(void* p){return static_cast<Experiment*>(p)->writer.roll.reason;}
}
