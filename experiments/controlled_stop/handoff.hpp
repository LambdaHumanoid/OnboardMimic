#pragma once
#include "core.hpp"
namespace sonic_bridge {
// Explicit experiment only. No automatic transfer on policy stop/fault.
struct ExperimentController:Controller {
 uint64_t pending_stream=0,expected_stream=0;double pending_until=0;Command frozen{};
 bool claim(uint64_t expected,uint64_t next,double now){
  advance(now);
  if(state!=State::active||now-last_valid>.02||expected!=stream||!next||next==stream||pending_stream)return false;
  pending_stream=next;expected_stream=expected;pending_until=now+.1;frozen=output;return true;
 }
 void cancel(){pending_stream=expected_stream=0;pending_until=0;}
 bool observe(const Packet& p,double now,const std::array<float,motors>* measured=nullptr){
  advance(now);
  if(pending_stream&&now>pending_until)cancel();
  if(pending_stream&&p.stream==pending_stream){
   if(state!=State::active||stream!=expected_stream||now-last_valid>.02||!measured){cancel();return false;}
   for(size_t i=0;i<29;i++){
    const auto& a=output.motor[i];const auto& b=p.cmd.motor[i];
    if(b.mode!=a.mode||std::abs(a.q-b.q)>1e-5||std::abs(a.kp-b.kp)>1e-5||std::abs(a.kd-b.kd)>1e-5||std::abs(a.tau-b.tau)>1e-5){cancel();return false;}
   }
   live=output=p.cmd;stream=p.stream;seq=p.seq;send_ns=p.send_ns;
   last_valid=now;state=State::active;cancel();return true;
  }
  bool result=Controller::observe(p,now,measured);
  if(result&&pending_stream&&p.stream==expected_stream&&state==State::active)output=frozen;
  return result;
 }
};
}
