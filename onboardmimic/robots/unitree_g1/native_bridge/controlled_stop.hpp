#pragma once
// Experimental, simulation-only. Not connected to the production writer.
// A joint-space trajectory is not a balance controller. Physical integration
// requires validated support estimation and a stabilizing fallback policy.
#include "core.hpp"

namespace sonic_bridge {
struct StopFeedback {
  std::array<float, motors> q{}, dq{};
  double captured=-1;
  float tilt=0;
  bool valid=false, fault=false, two_feet_supported=false;
};
enum class StopPhase { idle, lowering, releasing, relaxed, aborted };
inline const char* stop_name(StopPhase p) {
  switch(p) {
    case StopPhase::idle:return "idle";
    case StopPhase::lowering:return "lowering";
    case StopPhase::releasing:return "releasing";
    case StopPhase::relaxed:return "relaxed";
    default:return "aborted";
  }
}
struct ControlledStop {
  static constexpr double lower_seconds=3., release_seconds=.5;
  StopPhase phase=StopPhase::idle;
  Command output{}, initial{}, target{}, release_from{};
  double started=0, released_at=0, last_time=-1;
  const char* reason="";
  static double smooth(double t) {t=std::clamp(t,0.,1.);return t*t*(3.-2.*t);}
  static bool fresh(const StopFeedback& f,double now) {
    if(!std::isfinite(now)||!std::isfinite(f.captured)||!f.valid||f.fault||
       now<f.captured||now-f.captured>.05||!std::isfinite(f.tilt))return false;
    for(size_t i=0;i<29;++i)
      if(!std::isfinite(f.q[i])||!std::isfinite(f.dq[i]))return false;
    return true;
  }
  void abort(const char* why) {
    // Never resume automatically. Retain existing damping fallback semantics.
    output=damping(output);phase=StopPhase::aborted;reason=why;
  }
  bool begin(const Command& command,const StopFeedback& f,double now) {
    if(phase!=StopPhase::idle)return false;
    initial=output=command;
    if(!fresh(f,now)||!f.two_feet_supported||std::abs(f.tilt)>.15f) {
      abort("entry_feedback_or_support");return false;
    }
    if(command.mode_pr!=0) {abort("unsupported_ankle_mode");return false;}
    for(size_t i=0;i<29;++i) {
      const auto& m=command.motor[i];
      if(m.mode!=1||!std::isfinite(m.q)||!std::isfinite(m.dq)||
         !std::isfinite(m.kp)||!std::isfinite(m.kd)||!std::isfinite(m.tau)||
         m.kp<0||m.kp>500||m.kd<0||m.kd>10||
         std::abs(f.dq[i])>1.||std::abs(m.q-f.q[i])>.25) {
        abort("entry_joint_state");return false;
      }
    }
    target=command;
    for(size_t i=0;i<29;++i) {
      target.motor[i].q=f.q[i];target.motor[i].dq=0;target.motor[i].tau=0;
    }
    for(size_t offset:{size_t(0),size_t(6)}) {
      target.motor[offset].q=-.9f;
      target.motor[offset+1].q=0;target.motor[offset+2].q=0;
      target.motor[offset+3].q=1.7f;
      target.motor[offset+4].q=-.8f;target.motor[offset+5].q=0;
    }
    // Leave all hand/non-G1 channels untouched.
    started=last_time=now;phase=StopPhase::lowering;return true;
  }
  void tick(const StopFeedback& f,double now) {
    if(phase!=StopPhase::lowering&&phase!=StopPhase::releasing)return;
    if(!fresh(f,now)||now<last_time||now-last_time>.05||
       !f.two_feet_supported||std::abs(f.tilt)>.35f) {
      abort("feedback_support_or_timing");return;
    }
    for(size_t i=0;i<29;++i) {
      if(std::abs(f.dq[i])>4.||std::abs(f.q[i]-output.motor[i].q)>.5) {
        abort("joint_speed_or_tracking");return;
      }
    }
    last_time=now;
    if(phase==StopPhase::lowering) {
      output=blend(initial,target,smooth((now-started)/lower_seconds));
      if(now-started>=lower_seconds) {
        for(size_t i=0;i<29;++i)
          if(std::abs(f.q[i]-target.motor[i].q)>.15||std::abs(f.dq[i])>.6) {
            abort("did_not_settle");return;
          }
        release_from=output;released_at=now;phase=StopPhase::releasing;
      }
    } else {
      const float gain=1.-smooth((now-released_at)/release_seconds);
      output=release_from;
      for(size_t i=0;i<29;++i) {
        auto& m=output.motor[i];m.q=f.q[i];m.dq=0;
        m.kp*=gain;m.kd*=gain;m.tau*=gain;
      }
      if(now-released_at>=release_seconds)phase=StopPhase::relaxed;
      output.crc=crc(output);
    }
  }
};
}
