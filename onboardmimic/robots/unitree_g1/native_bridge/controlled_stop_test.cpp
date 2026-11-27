#include "controlled_stop.hpp"
#include <cassert>
#include <limits>
using namespace sonic_bridge;
StopFeedback feedback() {
 StopFeedback f;f.valid=true;f.two_feet_supported=true;f.captured=0;return f;
}
Command command() {
 Command c;for(size_t i=0;i<29;++i)c.motor[i]={1,0,0,0,40,2,0};return c;
}
int main() {
 ControlledStop s;auto f=feedback();auto c=command();
 assert(s.begin(c,f,0));assert(!s.begin(c,f,0));
 for(int i=1;i<=1751;++i) {
   f.captured=i*.002;
   for(size_t j=0;j<29;++j)f.q[j]=s.output.motor[j].q;
   s.tick(f,f.captured);assert(s.phase!=StopPhase::aborted);
 }
 assert(s.phase==StopPhase::relaxed);
 for(size_t j=0;j<29;++j)assert(s.output.motor[j].kp==0&&s.output.motor[j].kd==0&&s.output.motor[j].tau==0);
 for(int kind=0;kind<6;++kind) {
   ControlledStop x;auto v=feedback();assert(x.begin(c,v,0));
   v.captured=.002;
   if(kind==0)v.captured=-1;
   if(kind==1)v.fault=true;
   if(kind==2)v.two_feet_supported=false;
   if(kind==3)v.tilt=.4;
   if(kind==4)v.dq[0]=5;
   if(kind==5)v.q[0]=std::numeric_limits<float>::quiet_NaN();
   x.tick(v,.002);assert(x.phase==StopPhase::aborted);
   assert(!x.begin(c,feedback(),0));
 }
 ControlledStop immediate;f=feedback();assert(immediate.begin(c,f,0));
 immediate.abort("operator_estop");assert(immediate.phase==StopPhase::aborted);
 ControlledStop stale;f=feedback();assert(stale.begin(c,f,0));f.captured=.1;
 stale.tick(f,.1);assert(stale.phase==StopPhase::aborted);
}
