// Pure in-memory simulation adapter; no network, ROS or hardware.
#include "controlled_stop.hpp"
#include "policy_parameters.hpp"
using namespace sonic_bridge;
extern "C" {
void* stop_create() {return new ControlledStop;}
void stop_destroy(void* p) {delete static_cast<ControlledStop*>(p);}
void stop_gains(float* kp,float* kd) {
 for(int i=0;i<29;++i){kp[i]=kps[i];kd[i]=kds[i];}
}
int stop_step(void* p,double now,const float* q,const float* dq,float tilt,int support,int begin,float* out) {
 auto& c=*static_cast<ControlledStop*>(p);
 StopFeedback f;f.valid=true;f.two_feet_supported=support;f.captured=now;f.tilt=tilt;
 for(int i=0;i<29;++i){f.q[i]=q[i];f.dq[i]=dq[i];}
 if(begin) {
  Command command;
  for(int i=0;i<29;++i)command.motor[i]={1,q[i],0,0,kps[i],kds[i],0};
  c.begin(command,f,now);
 } else c.tick(f,now);
 for(int i=0;i<29;++i) {
  auto m=c.output.motor[i];out[5*i]=m.q;out[5*i+1]=m.dq;out[5*i+2]=m.tau;out[5*i+3]=m.kp;out[5*i+4]=m.kd;
 }
 return int(c.phase);
}
const char* stop_reason(void* p) {return static_cast<ControlledStop*>(p)->reason;}
}
