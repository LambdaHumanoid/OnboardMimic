#pragma once
#include "protection_roll.hpp"
#include "support.hpp"

namespace sonic_bridge {
// Decode validity per joint without inventing substitute angles. Source/tick
// ordering remains enforced by the receiver before committing this snapshot.
inline RollFeedback decode_protection_feedback(const msgpack::object& payload,
                                               double captured,double now,bool enabled){
 const auto& values=field(payload,"motor_state");
 if(values.type!=msgpack::type::ARRAY||values.via.array.size!=motors)
  throw std::runtime_error("motor count");
 RollFeedback f;f.captured=captured;f.valid=true;
 for(size_t i=0;i<motors;++i){
  f.q[i]=field(values.via.array.ptr[i],"q").as<float>();
  if(!std::isfinite(f.q[i])&&(!enabled||i>=29))throw std::runtime_error("nonfinite q");
 }
 if(enabled){
  for(size_t i=0;i<29;++i){
   f.dq[i]=field(values.via.array.ptr[i],"dq").as<float>();
   f.motor_faults[i]=field(values.via.array.ptr[i],"motorstate").as<uint32_t>();
   f.fault|=f.motor_faults[i]!=0;
   f.invalid_joints[i]=!std::isfinite(f.q[i])||!std::isfinite(f.dq[i]);
  }
  auto rpy=field(field(payload,"imu_state"),"rpy").as<std::vector<float>>();
  if(rpy.size()!=3)throw std::runtime_error("invalid IMU shape");
  f.roll=rpy[0];f.pitch=rpy[1];
  if(!ProtectionRoll::feedback_fresh(f,now))throw std::runtime_error("invalid protection feedback");
 }
 return f;
}
}
