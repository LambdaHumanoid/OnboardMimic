#include "core.hpp"
#include <cassert>
#include <iostream>
#include <fstream>
#include <iomanip>
// Undefining NDEBUG matters: tests must execute in Release builds too.
#undef assert
#define assert(x) do {if(!(x)){std::cerr<<"failed line "<<__LINE__<<": "<<#x<<'\n';return 1;}}while(0)
int main(int argc,char** argv){
 if(argc==2&&std::string(argv[1])=="trace") {
  sonic_bridge::Controller c;std::array<float,sonic_bridge::motors> q{};char op;double now;sonic_bridge::Packet p;
  while(std::cin>>op>>now>>p.stream>>p.seq>>p.send_ns) {
   bool apply=false;if(op=='p')apply=c.observe(p,now,&q);else c.advance(now);
   std::cout<<sonic_bridge::name(c.state)<<' '<<apply<<'\n';
  }
  return 0;
 }
 if((argc==3||argc==4)&&std::string(argv[1])=="decode"){
  std::array<uint8_t,sonic_bridge::wire_size> b{};std::ifstream f(argv[2],std::ios::binary);f.read(reinterpret_cast<char*>(b.data()),b.size());
  auto now=argc==4?std::stoull(argv[3]):sonic_bridge::read<uint64_t>(b.data()+24)+1000;
  sonic_bridge::Packet p;auto result=sonic_bridge::decode(b.data(),f.gcount(),now,p);
  std::cout<<int(result)<<' '<<sonic_bridge::crc(p.cmd)<<'\n';return result==sonic_bridge::Parse::ok?0:2;
 }
 sonic_bridge::Controller c;sonic_bridge::Packet p;p.stream=11;p.seq=1;p.send_ns=1;p.cmd.motor[0]={1,.5f,.1f,1.f,40.f,2.f,0};p.cmd.crc=sonic_bridge::crc(p.cmd);
 assert(c.state==sonic_bridge::State::idle);c.advance(1);assert(c.state==sonic_bridge::State::idle);
 assert(c.observe(p,1));assert(c.state==sonic_bridge::State::active);
 assert(!c.observe(p,1.02));auto q=p;q.stream=12;q.seq++;q.send_ns++;assert(!c.observe(q,1.03));
 c.advance(1.101);assert(c.state==sonic_bridge::State::failing);assert(!c.observe(p,1.11));
 c.advance(1.176);assert(std::abs(c.output.motor[0].kp-20)<1e-4);assert(std::abs(c.output.motor[0].kd-5)<1e-4);
 c.advance(1.252);assert(c.state==sonic_bridge::State::latched);assert(c.output.motor[0].kp==0);assert(c.output.motor[0].kd==8);
 for(int i=0;i<50;i++){p.seq++;p.send_ns++;assert(!c.observe(p,1.26+i*.002));}assert(c.state==sonic_bridge::State::latched);
 for(int i=0;i<50;i++){q.seq++;q.send_ns++;assert(!c.observe(q,1.4+i*.002));}assert(c.state==sonic_bridge::State::latched); // no fresh q
 std::array<float,sonic_bridge::motors> measured{};measured[0]=.25f;
 for(int i=0;i<24;i++){q.seq++;q.send_ns++;assert(!c.observe(q,2+i*.002,&measured));}
 q.seq++;q.send_ns++;assert(c.observe(q,2.048,&measured));assert(c.state==sonic_bridge::State::recovering);assert(c.output.motor[0].q==.25f);
 for(int i=1;i<=251;i++){q.seq++;q.send_ns++;assert(c.observe(q,2.048+i*.002,&measured));}
 assert(c.state==sonic_bridge::State::active);assert(c.output.motor[0].q==.5f);
 // New-stream validation must restart across a gap, and bad packets don't arm.
 sonic_bridge::Controller d;d.observe(p,0);d.advance(.101);d.advance(.252);
 for(int i=0;i<24;i++){q.seq++;q.send_ns++;d.observe(q,.3+i*.002,&measured);}
 q.seq++;q.send_ns++;assert(!d.observe(q,.45,&measured));assert(d.candidate_count==1);
 std::array<uint8_t,sonic_bridge::wire_size> b{};sonic_bridge::Packet z;assert(sonic_bridge::decode(b.data(),b.size(),1,z)==sonic_bridge::Parse::shape);
 std::cout<<"core watchdog and blend tests passed\n";
}
