#pragma once
// No ROS, network, logging, heap allocation or system calls in this core.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>

namespace sonic_bridge {
constexpr size_t motors = 35, wire_size = 931;
struct Motor { uint8_t mode=0; float q=0, dq=0, tau=0, kp=0, kd=0; uint32_t reserve=0; };
struct Command {
  uint8_t mode_pr=0, mode_machine=0;
  std::array<Motor, motors> motor{};
  std::array<uint32_t,4> reserve{};
  uint32_t crc=0;
};
struct Packet { uint64_t stream=0, seq=0, send_ns=0; Command cmd{}; };
inline uint32_t float_bits(float f) { uint32_t u; std::memcpy(&u,&f,4); return u; }
inline uint32_t crc(const Command& c) {
  // Unitree HG: <2B2x + (B3x5fI)*35 + 5I; CRC excludes last uint32.
  std::array<uint32_t,250> words{};
  words[0]=uint32_t(c.mode_pr)|(uint32_t(c.mode_machine)<<8);
  size_t k=1;
  for(const auto& m:c.motor) {
    words[k++]=m.mode; words[k++]=float_bits(m.q); words[k++]=float_bits(m.dq);
    words[k++]=float_bits(m.tau); words[k++]=float_bits(m.kp); words[k++]=float_bits(m.kd); words[k++]=m.reserve;
  }
  for(auto r:c.reserve) words[k++]=r;
  uint32_t result=0xffffffffu;
  for(auto word:words) for(uint32_t bit=0x80000000u;bit;bit>>=1) {
    bool high=(result&0x80000000u)!=0; result<<=1;
    if(high) result^=0x04c11db7u;
    if(word&bit) result^=0x04c11db7u;
  }
  return result;
}
template<class T> inline T read(const uint8_t* p) { T v; std::memcpy(&v,p,sizeof(T)); return v; }
enum class Parse { ok, shape, nonfinite, crc_error, stale };
inline Parse decode(const uint8_t* b,size_t n,uint64_t now,Packet& p) {
  // Onboard transport only: both sender and receiver use CLOCK_MONOTONIC.
  static_assert(__BYTE_ORDER__==__ORDER_LITTLE_ENDIAN__,"little-endian host required");
  if(n!=wire_size || std::memcmp(b,"LCU1",4) || read<uint16_t>(b+4)!=1 || b[34]!=35) return Parse::shape;
  p.stream=read<uint64_t>(b+8); p.seq=read<uint64_t>(b+16); p.send_ns=read<uint64_t>(b+24);
  if(p.send_ns>now || now-p.send_ns>100000000) return Parse::stale;
  p.cmd.mode_pr=b[32];p.cmd.mode_machine=b[33];
  for(size_t i=0;i<4;++i) p.cmd.reserve[i]=read<uint32_t>(b+36+4*i);
  p.cmd.crc=read<uint32_t>(b+52);
  for(size_t i=0;i<motors;++i) {
    auto& m=p.cmd.motor[i];auto v=b+56+i*25;m.mode=*v;
    m.q=read<float>(v+1);m.dq=read<float>(v+5);m.tau=read<float>(v+9);
    m.kp=read<float>(v+13);m.kd=read<float>(v+17);m.reserve=read<uint32_t>(v+21);
    if(!std::isfinite(m.q)||!std::isfinite(m.dq)||!std::isfinite(m.tau)||!std::isfinite(m.kp)||!std::isfinite(m.kd)) return Parse::nonfinite;
  }
  return p.cmd.crc==crc(p.cmd)?Parse::ok:Parse::crc_error;
}
enum class State { idle, active, failing, latched, recovering };
inline const char* name(State s) {
  switch(s) {case State::idle:return "idle";case State::active:return "active";
    case State::failing:return "failing";case State::latched:return "latched";default:return "recovering";}
}
inline Command damping(const Command& source,const std::array<float,motors>* measured=nullptr) {
  Command c; c.mode_pr=source.mode_pr;c.mode_machine=source.mode_machine;
  for(size_t i=0;i<motors;++i) {
    auto& m=c.motor[i]; m.mode=source.motor[i].mode?1:0;
    m.q=measured?(*measured)[i]:source.motor[i].q; m.kd=m.mode?8.f:0.f;
  }
  c.crc=crc(c);return c;
}
inline Command blend(const Command& a,const Command& b,double w) {
  w=std::min(1.,std::max(0.,w));Command c=b;
  for(size_t i=0;i<motors;++i) {
    auto& o=c.motor[i];auto& x=a.motor[i];auto& y=b.motor[i];
    o.q=(1-w)*x.q+w*y.q;o.dq=(1-w)*x.dq+w*y.dq;o.tau=(1-w)*x.tau+w*y.tau;
    o.kp=(1-w)*x.kp+w*y.kp;o.kd=(1-w)*x.kd+w*y.kd;
  }
  c.crc=crc(c);return c;
}
struct Controller {
  State state=State::idle;
  uint64_t stream=0, seq=0, send_ns=0, candidate=0, candidate_seq=0;
  double last_valid=-1, fail_at=0,recover_at=0,candidate_at=-1;
  unsigned candidate_count=0;
  Command live{},output{},fail_source{},fail_target{},recover_source{};
  void advance(double now) {
    if((state==State::active||state==State::recovering)&&now-last_valid>.1) {
      state=State::failing;fail_at=now;candidate_count=0;
      fail_source=output;fail_target=damping(output);
    } else if(state==State::failing&&now-fail_at>=.15) state=State::latched;
    if(state==State::failing) output=blend(fail_source,fail_target,(now-fail_at)/.15);
    if(state==State::latched) output=fail_target;
    if(state==State::recovering) output=blend(recover_source,live,(now-recover_at)/.5);
  }
  bool observe(const Packet& p,double now,const std::array<float,motors>* measured=nullptr) {
    advance(now);
    if(state==State::idle) {
      stream=p.stream;seq=p.seq;send_ns=p.send_ns;last_valid=now;live=output=p.cmd;state=State::active;return true;
    }
    if(state==State::active||state==State::recovering) {
      if(p.stream!=stream||p.seq<=seq||p.send_ns<=send_ns) return false;
      seq=p.seq;send_ns=p.send_ns;last_valid=now;live=p.cmd;
      if(state==State::recovering&&now-recover_at>=.5) state=State::active;
      output=state==State::active?live:blend(recover_source,live,(now-recover_at)/.5);
      return true;
    }
    // Failed stream cannot rearm. A replacement must prove freshness and supply
    // 25 consecutive packets plus fresh measured positions, then blend 0.5 s.
    if(state==State::failing||p.stream==stream||!measured) { candidate_count=0;return false; }
    if(candidate_count&&p.stream==candidate&&p.seq>candidate_seq&&now-candidate_at<=.05) ++candidate_count;
    else candidate_count=1;
    candidate=p.stream;candidate_seq=p.seq;candidate_at=now;
    if(candidate_count<25) return false;
    stream=p.stream;seq=p.seq;send_ns=p.send_ns;last_valid=now;live=p.cmd;
    recover_source=damping(live,measured);output=recover_source;recover_at=now;
    state=State::recovering;candidate_count=0;return true;
  }
};
}
