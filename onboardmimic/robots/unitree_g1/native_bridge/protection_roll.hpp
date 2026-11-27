#pragma once
// Candidate executor shared by offline fault injection and future motor writer.
// No simulator state, contact oracle, ROS, allocation, or online IK.
#include "core.hpp"
#include "roll_parameters.hpp"
#include "protection_kinematics.hpp"
#include "fault_profiles.hpp"
namespace sonic_bridge {
struct RollFeedback {
 std::array<float,motors> q{},dq{};
 double captured=-1,roll=0,pitch=0;
 bool valid=false,fault=false;
 std::array<uint32_t,motors> motor_faults{};
 std::array<bool,motors> invalid_joints{};
};
enum class RollPhase {idle,running,finished,aborted};
// Timestamped IMU history, independent of WBC process and simulator state.
struct PitchRateHistory {
 double previous_time=-1,previous_pitch=0,rate=0;
 int samples=0;
 void measure(double time,double pitch){
  if(!std::isfinite(time)||!std::isfinite(pitch))return;
  if(previous_time>=0&&time>previous_time&&time-previous_time<=.03){
   double dt=time-previous_time;
   double velocity=std::remainder(pitch-previous_pitch,2*3.141592653589793)/dt;
   rate+=(1-std::exp(-dt/.02))*(velocity-rate);samples=std::min(samples+1,1000);
  }else if(previous_time<0||time-previous_time>.03){samples=0;rate=0;}
  if(time>previous_time){previous_time=time;previous_pitch=pitch;}
 }
};
struct ProtectionRoll {
 RollPhase phase=RollPhase::idle;
 Command output{},source_command{};
 std::array<double,29> initial{},previous{},entry_velocity{},transfer_target{};
 double started=0,last=0;
 std::array<bool,29> braking{};
 std::array<double,29> recovery{};
 unsigned speed_brake_events=0;
 double ankle_bias=.5;
 double selected_pitch_rate=0;
 bool adaptive_bias_used=false;
 bool dynamic_entry=false;
 double entry_max_speed=0,foot_height_difference=0,transfer_s=0,crouch_s=0;
 int lower_foot=-1; // -1: similar height/unknown; 0/1: lower left/right ankle, not contact.
 const char* strategy="not_started";
 const char* reason="";
 static double smooth(double x){x=std::clamp(x,0.,1.);return x*x*x*(10+x*(-15+6*x));}
 static bool invalid_joint(const RollFeedback& f,size_t i){return f.invalid_joints[i]||!std::isfinite(f.q[i])||!std::isfinite(f.dq[i]);}
 static bool any_invalid(const RollFeedback& f){for(size_t i=0;i<29;++i)if(invalid_joint(f,i))return true;return false;}
 static bool frame_fresh(const RollFeedback& f,double now){return std::isfinite(now)&&std::isfinite(f.captured)&&f.valid&&now>=f.captured&&now-f.captured<=.03;}
 static bool feedback_fresh(const RollFeedback& f,double now){return frame_fresh(f,now)&&std::isfinite(f.roll)&&std::isfinite(f.pitch);}
 static bool fresh(const RollFeedback& f,double now){
  if(!feedback_fresh(f,now))return false;
  unsigned count=0;
  // Selected 0x200 fault profile: isolate the affected motor at any joint index.
  // Unknown/compound codes reject folding; invalid sensing is isolated separately.
  for(size_t i=0;i<motors;++i)if(f.motor_faults[i]||invalid_joint(f,i)){
   ++count;if(i>=29||(f.motor_faults[i]&&f.motor_faults[i]!=0x200)||!fault_profiles::standing_fold[i])return false;
  }
  return count<=1 && (!f.fault||count==1);
 }
 void abort(const char* why){output=damping(output);phase=RollPhase::aborted;reason=why;}
 bool begin(const Command& source,const RollFeedback& f,double now){
  if(phase!=RollPhase::idle)return false;
  output=source_command=source;
  if(feedback_fresh(f,now)&&!fresh(f,now)){abort("fault_profile_unavailable");return false;}
  if(!fresh(f,now)||source.mode_pr!=0){abort("entry_state");return false;}
  for(size_t i=0;i<29;++i){
   if(invalid_joint(f,i)){initial[i]=previous[i]=entry_velocity[i]=0;output.motor[i]=Motor{};continue;}
   const auto& m=source.motor[i];
   if(m.mode!=1||!std::isfinite(m.q)||!std::isfinite(m.dq)||!std::isfinite(m.kp)||!std::isfinite(m.kd)||!std::isfinite(m.tau)||m.kp<0||m.kp>500||m.kd<0||m.kd>10||f.q[i]<roll_parameters::qmin[i]-.12||f.q[i]>roll_parameters::qmax[i]+.12){abort("entry_invalid_joint");return false;}
   initial[i]=previous[i]=std::clamp(double(f.q[i]),roll_parameters::qmin[i],roll_parameters::qmax[i]);
   entry_velocity[i]=std::clamp(double(f.dq[i]),-5.,5.);
   entry_max_speed=std::max(entry_max_speed,std::abs(double(f.dq[i])));
  }
  if((f.fault||any_invalid(f))&&(entry_max_speed>2.||std::abs(f.roll)>.2||std::abs(f.pitch)>.25)){
   abort("fault_entry_outside_standing_profile");return false;
  }
  if(any_invalid(f)){
   // Evaluate only relations whose inputs remain valid. No substituted angle.
   bool legs_valid=true;for(size_t i=0;i<12;++i)legs_valid&=!invalid_joint(f,i);
   foot_height_difference=0;lower_foot=-1;
   if(legs_valid){
    foot_height_difference=protection_kinematics::foot_height(f.q,f.roll,f.pitch,0)-protection_kinematics::foot_height(f.q,f.roll,f.pitch,1);
    lower_foot=std::abs(foot_height_difference)>.025?(foot_height_difference<0?0:1):-1;
   }
   bool hip_asymmetry=!invalid_joint(f,0)&&!invalid_joint(f,6)&&std::abs(f.q[0]-f.q[6])>.2;
   bool knee_asymmetry=!invalid_joint(f,3)&&!invalid_joint(f,9)&&std::abs(f.q[3]-f.q[9])>.2;
   dynamic_entry=lower_foot!=-1||hip_asymmetry||knee_asymmetry;
   strategy="single_feedback_loss_fold";
  }else{
  foot_height_difference=protection_kinematics::foot_height(f.q,f.roll,f.pitch,0)-protection_kinematics::foot_height(f.q,f.roll,f.pitch,1);
  lower_foot=std::abs(foot_height_difference)>.025?(foot_height_difference<0?0:1):-1;
  dynamic_entry=entry_max_speed>2.||lower_foot!=-1||std::abs(f.q[0]-f.q[6])>.2||std::abs(f.q[3]-f.q[9])>.2||std::abs(f.roll)>.2||std::abs(f.pitch)>.25;
  strategy=dynamic_entry?"moving_fold_transfer":"standing_fold";
  }
  transfer_s=dynamic_entry?roll_parameters::dynamic_transfer_s:0.;
  crouch_s=dynamic_entry?roll_parameters::dynamic_crouch_s:roll_parameters::first_stage_s;
  if(std::abs(f.roll)>.5||std::abs(f.pitch)>.6){transfer_s=0.;crouch_s=.6;strategy="tilted_compact_catch";}
  for(size_t i=0;i<29;++i){
   transfer_target[i]=std::clamp(initial[i]+entry_velocity[i]*transfer_s*.5,roll_parameters::qmin[i],roll_parameters::qmax[i]);
   if(transfer_s>0&&dynamic_entry&&lower_foot!=-1&&i==size_t((1-lower_foot)*6+3))transfer_target[i]=std::max(roll_parameters::qmin[i],transfer_target[i]-roll_parameters::dynamic_swing_extension);
  }
  started=last=now;if(entry_max_speed>15.)last=now-.002;phase=RollPhase::running;return true;
 }
 void tick(const RollFeedback& f,double now){
  if(phase!=RollPhase::running)return;
  if(!fresh(f,now)||now<last||now-last>.02){abort("feedback_or_deadline");return;}
  for(size_t i=0;i<29;++i)if(!invalid_joint(f,i)&&(f.q[i]<roll_parameters::qmin[i]-.12||f.q[i]>roll_parameters::qmax[i]+.12)){abort("joint_envelope");return;}
  double dt=now-last,t=now-started;if(dt<=0)return;
  last=now;
  if(t>=7.){output=damping(output);phase=RollPhase::finished;reason="trajectory_complete_not_contact_verified";return;}
  // Hysteresis prevents rapid toggling around the speed threshold. Only the
  // overspeed joints brake; other joints keep folding. No permanent abort.
  for(size_t i=0;i<29;++i){
   if(invalid_joint(f,i)){braking[i]=false;recovery[i]=0;continue;}
   double speed=std::abs(double(f.dq[i]));
   if(speed>15.&&!braking[i]){braking[i]=true;recovery[i]=0;++speed_brake_events;}
   if(braking[i]&&speed<10.){braking[i]=false;recovery[i]=.12;previous[i]=std::clamp(double(f.q[i]),roll_parameters::qmin[i],roll_parameters::qmax[i]);}
  }
  for(size_t i=0;i<29;++i){
   if(invalid_joint(f,i)){output.motor[i]=Motor{};continue;}
   if(braking[i]){
    auto& m=output.motor[i];m.q=std::clamp(double(f.q[i]),roll_parameters::qmin[i],roll_parameters::qmax[i]);
    m.dq=0;m.kp=0;m.tau=0;
    m.kd=std::min(roll_parameters::kd[i],.6*roll_parameters::effort[i]/std::max(1.,std::abs(double(f.dq[i]))));
    previous[i]=m.q;continue;
   }
   double target;
   const double t1=crouch_s,t2=t1+roll_parameters::second_stage_s-roll_parameters::first_stage_s,t3=t2+roll_parameters::tuck_stage_s-roll_parameters::second_stage_s;
   const double fold_time=(i==0||i==6||i==14||i>=15)?t1*roll_parameters::upper_fold_fraction:t1;
   double motion_time=std::max(0.,t-transfer_s);
   if(t<transfer_s){double u=t/transfer_s;target=(2*u*u*u-3*u*u+1)*initial[i]+(u*u*u-2*u*u+u)*transfer_s*entry_velocity[i]+(-2*u*u*u+3*u*u)*transfer_target[i];}
   else if(motion_time<t1){double start=dynamic_entry?transfer_target[i]:initial[i];target=start+smooth(motion_time/fold_time)*(roll_parameters::pose1[i]-start);}
   else if(motion_time<t2)target=roll_parameters::pose1[i]+smooth((motion_time-t1)/(t2-t1))*(roll_parameters::pose2[i]-roll_parameters::pose1[i]);
   else target=roll_parameters::pose2[i]+smooth((motion_time-t2)/(t3-t2))*(roll_parameters::pose3[i]-roll_parameters::pose2[i]);
   double backward_time=t-transfer_s-roll_parameters::ankle_start_s;
   if(i==4||i==10)target+=ankle_bias*smooth(backward_time/.1)*(1-smooth((backward_time-.3)/.4));
   target=std::clamp(target,roll_parameters::qmin[i],roll_parameters::qmax[i]);
   target=std::clamp(target,previous[i]-5.*dt,previous[i]+5.*dt);
   double velocity=(target-previous[i])/dt;previous[i]=target;
   double w=smooth(t/.05);
   auto& m=output.motor[i];const auto& old=source_command.motor[i];
   m.q=std::clamp((1-w)*old.q+w*target,roll_parameters::qmin[i],roll_parameters::qmax[i]);
   m.dq=std::clamp((1-w)*old.dq+w*velocity,-5.,5.);
   m.kp=(1-w)*old.kp+w*roll_parameters::kp[i];
   m.kd=(1-w)*old.kd+w*roll_parameters::kd[i];m.tau=(1-w)*old.tau;
   if(recovery[i]>0){
    double blend=smooth(1-recovery[i]/.12);
    m.kp*=blend;m.tau*=blend;m.dq*=blend;
    m.kd=blend*m.kd+(1-blend)*std::min(roll_parameters::kd[i],.6*roll_parameters::effort[i]/std::max(1.,std::abs(double(f.dq[i]))));
    recovery[i]=std::max(0.,recovery[i]-dt);
   }
   // Local motor damping remains active despite delayed network feedback.
   // Scale gains rather than moving q outside the mechanical limits.
   // This bounds predicted torque; actuator effort limits still apply locally.
   double predicted=m.tau+m.kp*(m.q-f.q[i])+m.kd*(m.dq-f.dq[i]);
   double scale=std::min(1.,roll_parameters::effort[i]/std::max(1e-9,std::abs(predicted)));
   m.kp*=scale;m.kd*=scale;m.tau*=scale;
  }
  output.crc=crc(output);
 }
};
// Writer-side fencing: after takeover neither delayed nor replacement WBC
// streams can resume. Deliberate reset requires a new monotonically increasing
// generation, and cannot interrupt an executing trajectory.
struct ProtectedWriter {
 Controller wbc;
 ProtectionRoll roll;
 PitchRateHistory pitch_history;
 uint64_t generation=0,armed_stream=0;
 bool watching=false,armed=false,fenced=false,estopped=false,takeover_requested=false;
 std::array<uint32_t,motors> disabled_faults{};
 std::array<bool,motors> disabled_feedback{};
 mutable Command masked_output{};
 double timeout=.05,session_started=0;
 bool reset(uint64_t next,double now=0){
  if(!std::isfinite(now)||now<0||next<=generation||watching||armed||roll.phase==RollPhase::running)return false;
  generation=next;session_started=now;armed_stream=0;watching=armed=fenced=estopped=takeover_requested=false;
  wbc=Controller{};roll=ProtectionRoll{};pitch_history=PitchRateHistory{};return true;
 }
 bool arm(uint64_t expected,uint64_t stream,const RollFeedback& f,double now){
  if(expected!=generation||!generation||fenced||estopped||watching||armed||f.fault||ProtectionRoll::any_invalid(f)||std::any_of(f.motor_faults.begin(),f.motor_faults.end(),[](uint32_t x){return x!=0;})||!ProtectionRoll::fresh(f,now))return false;
  if(wbc.state==State::idle&&stream==0){disabled_faults.fill(0);disabled_feedback.fill(false);watching=true;return true;}
  if(wbc.state!=State::active||stream!=wbc.stream||now-wbc.last_valid>timeout)return false;
  disabled_faults.fill(0);disabled_feedback.fill(false);
  watching=armed=true;armed_stream=stream;return true;
 }
 bool observe(const Packet& p,double now,const RollFeedback& f){
  if(fenced||estopped||!std::isfinite(now)||p.send_ns/1e9<session_started||p.send_ns>now*1e9||now*1e9-p.send_ns>50000000)return false;
  // Expiry is checked before accepting a late packet, not just on writer ticks.
  advance(f,now);
  if(fenced||estopped)return false;
  if(ProtectionRoll::any_invalid(f))return false;
  const bool accepted=wbc.observe(p,now,ProtectionRoll::fresh(f,now)?&f.q:nullptr);
  if(accepted&&watching&&!armed&&wbc.state==State::active){armed=true;armed_stream=wbc.stream;}
  return accepted;
 }
 void cancel(uint64_t expected){
  if(expected!=generation)return;
  watching=armed=false;fenced=true;estopped=true;
  if(roll.phase==RollPhase::running)roll.abort("intentional_stop");
  wbc.output=damping(wbc.output);
 }
 void advance(const RollFeedback& input,double now,bool process_exited=false){
  RollFeedback f=input;
  if(ProtectionRoll::frame_fresh(f,now))for(size_t i=0;i<motors;++i){disabled_faults[i]|=f.motor_faults[i];disabled_feedback[i]=disabled_feedback[i]||ProtectionRoll::invalid_joint(f,i);}
  // A cleared fault bit never re-enables its motor within this session. Use
  // the same latched set for profile admission, including subsequent faults.
  for(size_t i=0;i<motors;++i){f.invalid_joints[i]=f.invalid_joints[i]||disabled_feedback[i];f.motor_faults[i]|=disabled_faults[i];f.fault|=f.motor_faults[i]!=0;}
  if(estopped)return;
  if(!fenced){
   if(ProtectionRoll::fresh(f,now))pitch_history.measure(f.captured,f.pitch);
   else pitch_history=PitchRateHistory{};
  }
  if(watching&&!armed&&!fenced&&process_exited){
   takeover_requested=true;fenced=true;watching=false;
   roll.output=wbc.output;roll.abort("producer_exit_before_first_output");return;
  }
  if(armed&&!fenced&&(process_exited||ProtectionRoll::any_invalid(f)||now-wbc.last_valid>timeout)){
   takeover_requested=true;
   if(roll_parameters::adaptive_ankle_enabled&&ProtectionRoll::fresh(f,now)&&pitch_history.samples>=3){
    roll.selected_pitch_rate=pitch_history.rate;
    roll.ankle_bias=std::clamp(roll_parameters::adaptive_ankle_base+roll_parameters::adaptive_ankle_rate_gain*pitch_history.rate+roll_parameters::adaptive_ankle_pitch_gain*f.pitch,0.,roll_parameters::adaptive_ankle_maximum);
    roll.adaptive_bias_used=true;
   }
   fenced=true;watching=armed=false;
   roll.begin(wbc.output,f,now);
  }
  if(fenced)roll.tick(f,now);else wbc.advance(now);
 }
 const Command& output()const{
  masked_output=fenced&&!estopped?roll.output:wbc.output;
  for(size_t i=0;i<motors;++i)if(disabled_faults[i]||disabled_feedback[i])masked_output.motor[i]=Motor{};
  masked_output.crc=crc(masked_output);return masked_output;
 }
};
}
