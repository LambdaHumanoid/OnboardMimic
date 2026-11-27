#include "handoff.hpp"
#include <cassert>
int main(){
 using namespace sonic_bridge;ExperimentController c;Packet a;a.stream=1;a.seq=1;a.send_ns=1;a.cmd.motor[0].mode=1;a.cmd.motor[0].kp=50;
 std::array<float,motors> q{};assert(c.observe(a,1,&q));assert(!c.claim(7,2,1));assert(c.claim(1,2,1));
 Packet b=a;b.stream=2;b.seq=1;b.send_ns=2;b.cmd.motor[0].q=0;
 assert(c.observe(b,1.002,&q));assert(c.output.motor[0].q==0);assert(c.stream==2);assert(c.state==State::active);
 a.seq++;a.send_ns=3;assert(!c.observe(a,1.004,&q)); // former owner cannot steal back
 for(int i=1;i<=300;i++){b.seq++;b.send_ns++;assert(c.observe(b,1.002+i*.002,&q));}
 assert(c.state==State::active);assert(c.output.motor[0].q==b.cmd.motor[0].q);
 c.advance(2);assert(c.state==State::failing);c.advance(2.16);assert(c.state==State::latched);
 ExperimentController d;assert(d.observe(a,1,&q));assert(d.claim(1,2,1));b.cmd.motor[0].q=1;assert(!d.observe(b,1.001,&q));assert(d.stream==1);
 assert(d.claim(1,2,1.002));a.seq++;a.send_ns++;assert(d.observe(a,1.15,&q)==false); // watchdog, not a stale transfer
 ExperimentController e;assert(e.observe(a,1,&q));assert(e.claim(1,2,1));e.cancel();b.cmd.motor[0].q=0;assert(!e.observe(b,1.001,&q));
}
