#include "protection_roll.hpp"
#include "protection_voice_signal.hpp"
#include "protection_voice_pcm.hpp"
#include <cassert>
#include <limits>
#include <iostream>
using namespace sonic_bridge;
RollFeedback feedback(double t){RollFeedback f;f.valid=true;f.captured=t;for(size_t i=0;i<29;++i)f.q[i]=std::clamp(0.,roll_parameters::qmin[i],roll_parameters::qmax[i]);f.q[3]=f.q[9]=.67;f.q[0]=f.q[6]=-.31;f.q[4]=f.q[10]=-.36;return f;}
Packet packet(double t,uint64_t seq=1,uint64_t stream=42){Packet p;p.stream=stream;p.seq=seq;p.send_ns=uint64_t(t*1e9);auto f=feedback(t);for(size_t i=0;i<29;++i)p.cmd.motor[i]={1,f.q[i],0,0,40,3,0};p.cmd.crc=crc(p.cmd);return p;}
ProtectedWriter armed(){ProtectedWriter w;assert(w.reset(1));assert(w.observe(packet(1),1,feedback(1)));assert(w.arm(1,42,feedback(1),1));return w;}
int main(){
 {auto w=armed();w.advance(feedback(1.04),1.04);assert(!w.fenced);w.advance(feedback(1.052),1.052);assert(w.fenced&&w.roll.phase==RollPhase::running);assert(!w.observe(packet(1.054,2),1.054,feedback(1.054)));assert(!w.observe(packet(1.054,1,99),1.054,feedback(1.054)));assert(!w.reset(2));}
 {auto w=armed();assert(!w.observe(packet(1.06,2),1.06,feedback(1.06)));assert(w.fenced);}
 {auto w=armed();assert(!w.observe(packet(1,1),1.02,feedback(1.02)));w.advance(feedback(1.052),1.052);assert(w.fenced);}
 {auto w=armed();w.advance(feedback(1.002),1.002,true);assert(w.fenced);assert(w.roll.phase==RollPhase::running);}
 {auto w=armed();w.cancel(1);w.advance(feedback(1.2),1.2,true);assert(w.estopped&&w.roll.phase==RollPhase::idle);assert(!w.observe(packet(1.2,4),1.2,feedback(1.2)));}
 {auto w=armed();w.cancel(0);assert(!w.estopped);w.advance(feedback(1.002),1.002,true);w.cancel(1);assert(w.roll.phase==RollPhase::aborted);for(auto m:w.output().motor)assert(m.kp==0&&m.tau==0);assert(w.reset(2));assert(!w.armed&&!w.fenced);assert(!w.reset(1));}
 {auto w=armed();w.advance(feedback(1),1.052);assert(w.roll.phase==RollPhase::aborted);assert(w.fenced);}
 {auto w=armed();auto f=feedback(1.01);f.fault=true;w.advance(f,1.01,true);assert(w.roll.phase==RollPhase::aborted);}
 {auto w=armed();auto f=feedback(1.01);f.pitch=.8;w.advance(f,1.01,true);assert(w.roll.phase==RollPhase::running);assert(std::string(w.roll.strategy)=="tilted_compact_catch");}
 {auto w=armed();w.advance(feedback(1.002),1.002,true);auto f=feedback(1.004);f.q[0]=std::numeric_limits<float>::quiet_NaN();w.advance(f,1.004);assert(w.roll.phase==RollPhase::running);assert(w.disabled_feedback[0]&&w.output().motor[0].mode==0);}
 {auto w=armed();w.advance(feedback(1.002),1.002,true);w.advance(feedback(1.05),1.05);assert(w.roll.phase==RollPhase::aborted);}
 {auto w=armed();assert(!w.arm(0,42,feedback(1),1));assert(!w.arm(1,99,feedback(1),1));}
 {ProtectedWriter w;assert(w.reset(1));w.observe(packet(1),1,feedback(1));w.advance(feedback(1.2),1.2,true);assert(!w.fenced&&w.roll.phase==RollPhase::idle);}
 {auto w=armed();w.advance(feedback(1.002),1.002,true);for(int n=2;n<3503;++n){double t=1+n*.002;auto f=feedback(t);w.advance(f,t);const auto& c=w.output();assert(c.crc==crc(c));for(size_t i=0;i<29;++i){const auto& m=c.motor[i];double tau=m.tau+m.kp*(m.q-f.q[i])+m.kd*(m.dq-f.dq[i]);assert(std::isfinite(tau));assert(std::abs(tau)<=roll_parameters::effort[i]+1e-3);}}assert(w.roll.phase==RollPhase::finished);assert(!w.observe(packet(9,50,200),9,feedback(9)));}
 {auto w=armed();w.cancel(1);assert(w.reset(2,9.));assert(!w.observe(packet(8.99,2,99),9.,feedback(9.)));assert(w.observe(packet(9.002,3,99),9.002,feedback(9.002)));}
 {PitchRateHistory h;h.measure(0,0);for(int i=1;i<=25;++i)h.measure(i*.002,i*.002*.2);assert(h.samples==25&&h.rate>.18&&h.rate<.2);auto rate=h.rate;h.measure(.05,100);h.measure(.04,100);assert(h.rate==rate&&h.samples==25);h.measure(.1,.02);assert(h.samples==0&&h.rate==0);}
 {PitchRateHistory h;h.measure(0,3.14);h.measure(.002,-3.14);assert(std::abs(h.rate)<1);h.measure(std::numeric_limits<double>::quiet_NaN(),0);assert(h.samples==1);}
 {for(double velocity:{-.5,0.,.5}){auto w=armed();for(int i=1;i<=25;++i){double t=1+i*.002;auto f=feedback(t);f.pitch=velocity*i*.002;w.advance(f,t);}auto f=feedback(1.052);f.pitch=velocity*.052;w.advance(f,1.052,true);assert(w.roll.adaptive_bias_used);double selected=w.roll.ankle_bias;assert(selected>=0&&selected<=.5);if(velocity<0)assert(selected==0);if(velocity==0)assert(std::abs(selected-.25)<1e-9);if(velocity>0)assert(selected==.5);f=feedback(1.054);f.pitch=-velocity*.054;w.advance(f,1.054);assert(w.roll.ankle_bias==selected);assert(!w.reset(2));w.cancel(1);assert(w.reset(2));assert(w.pitch_history.samples==0&&!w.roll.adaptive_bias_used);}}
 {auto w=armed();w.advance(feedback(1.002),1.002,true);assert(!w.roll.adaptive_bias_used&&w.roll.ankle_bias==.5);}
 {auto w=armed();for(int i=1;i<=8;++i)w.advance(feedback(1+i*.002),1+i*.002);auto f=feedback(1.018);f.fault=true;w.advance(f,1.018);assert(w.pitch_history.samples==0);w.advance(feedback(1.02),1.02,true);assert(!w.roll.adaptive_bias_used);}
 {ProtectionVoiceSignal s;assert(!s.notify(0,true));assert(!s.notify(1,false));assert(s.pending.load()==0);assert(s.notify(1,true));for(int i=0;i<100000;++i)assert(!s.notify(1,true));assert(s.pending.load()==1);assert(!s.notify(2,false));assert(!s.notify(1,false));assert(s.notify(2,true));assert(s.pending.load()==2);assert(!s.notify(1,true));}
 {static_assert(protection_voice_pcm.size()>0&&protection_voice_pcm.size()%2==0&&protection_voice_pcm.size()<=320000,"Each bilingual status announcement must be at most 10 seconds");}
 {ProtectedWriter w;assert(w.reset(1,1));assert(w.arm(1,0,feedback(1),1));assert(w.watching&&!w.armed);assert(!w.reset(2));w.advance(feedback(2),2);assert(!w.fenced);assert(w.observe(packet(2.002),2.002,feedback(2.002)));assert(w.armed);auto f=feedback(2.004);f.dq[3]=4.;w.advance(f,2.004,true);assert(w.takeover_requested&&w.roll.phase==RollPhase::running&&w.roll.dynamic_entry);for(int i=3;i<100;++i){auto measured=feedback(2+i*.002);measured.dq[3]=4.;w.advance(measured,2+i*.002);for(size_t j=0;j<29;++j){auto m=w.output().motor[j];assert(m.q>=roll_parameters::qmin[j]-.00001&&m.q<=roll_parameters::qmax[j]+.00001);assert(std::abs(m.tau+m.kp*(m.q-measured.q[j])+m.kd*(m.dq-measured.dq[j]))<=roll_parameters::effort[j]+.001);}}}
 {ProtectedWriter w;assert(w.reset(1));assert(w.arm(1,0,feedback(1),1));w.advance(feedback(1.002),1.002,true);assert(w.takeover_requested&&w.fenced&&w.roll.phase==RollPhase::aborted&&w.wbc.state==State::idle);for(auto m:w.output().motor)assert(m.mode==0);}
 {ProtectedWriter w;assert(w.reset(1));assert(w.arm(1,0,feedback(1),1));w.cancel(1);w.advance(feedback(1.002),1.002,true);assert(!w.takeover_requested&&w.estopped&&!w.watching);}
 {auto w=armed();auto f=feedback(1.002);f.dq[3]=16.;w.advance(f,1.002,true);assert(w.takeover_requested&&w.roll.phase==RollPhase::running);f.captured=1.004;w.advance(f,1.004);assert(w.roll.braking[3]);auto m=w.output().motor[3];assert(m.kp==0&&m.tau==0&&m.dq==0);assert(m.kd*16<=.6*roll_parameters::effort[3]+.001);f.dq[3]=9;f.captured=1.006;w.advance(f,1.006);assert(!w.roll.braking[3]&&w.roll.recovery[3]>0);}
 {auto w=armed();auto f=feedback(1.002);f.q[3]=1.2;w.advance(f,1.002,true);assert(w.roll.dynamic_entry&&w.roll.lower_foot==1);assert(w.roll.transfer_target[3]<w.roll.initial[3]);}
 // Repeated in-motion overspeed: torque opposes velocity, recovery is hysteretic,
 // unrelated joints remain controlled, and stale feedback still aborts.
 {for(double speed:{-24.,16.,30.}){auto w=armed();w.advance(feedback(1.002),1.002,true);
  auto f=feedback(1.004);f.dq[3]=speed;w.advance(f,1.004);
  assert(w.roll.phase==RollPhase::running&&w.roll.braking[3]);
  auto m=w.output().motor[3];double tau=m.kd*(m.dq-f.dq[3]);assert(tau*speed<=0&&std::abs(tau)<=.6*roll_parameters::effort[3]+.001);
  assert(w.output().motor[0].kp>0);
  f.captured=1.006;f.dq[3]=12;w.advance(f,1.006);assert(w.roll.braking[3]);
  f.captured=1.008;f.dq[3]=9;w.advance(f,1.008);assert(!w.roll.braking[3]);
  f.captured=1.010;f.dq[3]=speed;w.advance(f,1.010);assert(w.roll.braking[3]&&w.roll.speed_brake_events==2);
  w.advance(f,1.05);assert(w.roll.phase==RollPhase::aborted);
 }}
 {auto w=armed();auto f=feedback(1.002);f.dq[3]=20;w.advance(f,1.002,true);assert(w.roll.braking[3]);w.cancel(1);assert(w.estopped&&w.roll.phase==RollPhase::aborted);}
 std::cout<<"29 protection executor/fence/history/voice/continuous-watch scenarios passed; no hardware\n";
}
