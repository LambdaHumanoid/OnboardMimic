#pragma once
// SONIC-owned schedule. Recorded start pose is convergence evidence only;
// recorded states/history never replace live decoder observations.
// Parsing/socket work is off the control thread; the control thread only copies token64.
#include "native_consumption.hpp"
#include "control_start_safety.hpp"
#include <mutex>
#include <cmath>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <unistd.h>
#include <poll.h>
#include <cstring>
#include <ctime>
#include <sys/syscall.h>
#include <fcntl.h>
#include <cstdio>
namespace sonic_replay {
using Json=sonic_consumption::Json;
// SONIC Planner produces G1 references (encoder mode 0), not SMPL mode 1.
constexpr int planner_encoder_mode=0;
inline bool planner_return_admissible(bool encoder,bool planner_motion,int mode){
 return encoder&&planner_motion&&mode==planner_encoder_mode;
}
constexpr uint64_t lease_ns=500000000, warm_ns=1000000000, blend_ns=2000000000;
constexpr uint64_t settle_ns=200000000, entry_timeout_ns=6000000000;
struct Frame {uint64_t offset=0,due=0;std::array<double,64> token{};};
struct Feedback {bool fresh=false;std::array<double,29> q{},dq{},target{};std::array<double,3> gravity{};};
struct Plan {std::vector<Frame> frames;uint64_t period=20000000;std::array<double,29> start_q{};std::array<double,3> start_gravity{};};
inline bool aligned(const Plan& p,const Feedback& f){
 if(!f.fresh)return false;
 double error=0;
 // A token is a reference, not the recorded instantaneous measured pose.
 // PD position error also supplies supporting torque, so it is not a pose
 // convergence metric. Require settled live motion and base attitude instead.
 for(size_t i=0;i<29;++i)if(!control_start_safety::IsFinite(f.q[i])||!control_start_safety::IsFinite(f.dq[i])||!control_start_safety::IsFinite(f.target[i])||std::abs(f.dq[i])>.6)return false;
 for(size_t i=0;i<3;++i){if(!control_start_safety::IsFinite(f.gravity[i]))return false;error+=std::pow(f.gravity[i]-p.start_gravity[i],2);}
 return error<=.15*.15;
}
struct Tick {bool stop=false,begin=false,external=false;uint64_t run=0,frame=0;bool consumed=false,returning=false;double planner_blend=0;double blend=0;std::array<double,64> token{};};
inline uint64_t number(const Json& j){if(!j.is_number_integer()||j.get<double>()<0)throw std::runtime_error("nonnegative integer required");return j.get<uint64_t>();}
inline std::shared_ptr<const Plan> parse(const Json& j){
 if(j.at("entry_protocol")!=3)throw std::runtime_error("entry protocol mismatch");
 auto p=std::make_shared<Plan>();p->period=number(j.at("period_ns"));if(p->period!=20000000)throw std::runtime_error("SONIC 50Hz required");
 auto vector=[](const Json& a,auto& out){if(!a.is_array()||a.size()!=out.size())throw std::runtime_error("invalid entry target");for(size_t i=0;i<out.size();++i){if(!a[i].is_number()||!control_start_safety::IsFinite(a[i].get<double>()))throw std::runtime_error("invalid entry target");out[i]=a[i].get<double>();}};
 vector(j.at("start_q"),p->start_q);vector(j.at("start_gravity"),p->start_gravity);
 double norm=0;for(auto v:p->start_gravity)norm+=v*v;if(norm<.9||norm>1.1)throw std::runtime_error("invalid entry gravity");
 const auto& rows=j.at("frames");if(!rows.is_array()||rows.size()<2)throw std::runtime_error("invalid frame count");
 for(const auto& row:rows){Frame f;f.offset=number(row.at("offset_ns"));const auto& token=row.at("token");if(!token.is_array()||token.size()!=64)throw std::runtime_error("token64 required");
  for(size_t i=0;i<64;++i){if(!token[i].is_number())throw std::runtime_error("numeric token required");f.token[i]=token[i].get<double>();if(!control_start_safety::IsFinite(f.token[i])||!control_start_safety::IsFinite(float(f.token[i])))throw std::runtime_error("nonfinite token");}
  if(p->frames.empty()){if(f.offset)throw std::runtime_error("first offset must be zero");}
  else if(f.offset<=p->frames.back().offset||f.offset-p->frames.back().offset>60000000)throw std::runtime_error("discontinuous plan");
  if(f.offset>UINT64_MAX-p->period)throw std::runtime_error("source time overflow");
  // Source timestamps are evidence only; execute every token once at 50 Hz.
  if(p->frames.size()>=UINT64_MAX/p->period)throw std::runtime_error("schedule time overflow");
  f.due=p->frames.size()*p->period;
  p->frames.push_back(f);
 }
 return p;
}
// All methods serialized by Service. Exposed for deterministic CPU tests.
struct Core {
 std::shared_ptr<const Plan> plan;uint64_t run=0,deadline=0,start=0,last_tick=0,seq=0;size_t cursor=0;
 bool armed=false,begun=false,terminal=false,selected=false;std::string reason;
 bool operator_return=false;double return_weight=1.;
 bool finish_requested=false,completed=false;uint64_t return_start=0,return_ready=0;
 uint64_t tick_gap_ns=0,schedule_lateness_ns=0,schedule_frame=0;
 // Bounded phase correction, not faster catch-up or skipped source frames.
 uint64_t schedule_shift_ns=0,shift_window_ns=0,rebase_count=0;
 std::array<std::pair<uint64_t,uint64_t>,10> corrections{};std::string overrun_limit;
 uint64_t callback_cpu_ns=0,command_deferrals=0,snapshot_deferrals=0;
 uint64_t sched_sample_ns=0,sched_wait_ns=0,sched_cpu_ns=0;
 struct Timing {uint64_t at=0,gap=0,cpu=0,frame=0,late=0,shift=0,sample=0,wait=0,runtime=0;};
 std::array<Timing,16> recent{};size_t recent_count=0;
 void record_timing(uint64_t now){recent[recent_count++%recent.size()]={now,tick_gap_ns,callback_cpu_ns,schedule_frame,schedule_lateness_ns,schedule_shift_ns,sched_sample_ns,sched_wait_ns,sched_cpu_ns};}
 uint64_t stable_since=0,play_start=0;std::string phase="loaded";
 void load(std::shared_ptr<const Plan> p){if(armed||terminal)throw std::runtime_error("new native process required after replay");*this=Core{};plan=std::move(p);}
 void arm(uint64_t id,uint64_t now){if(!plan||armed||terminal||!id)throw std::runtime_error("cannot start");run=id;armed=true;deadline=now+lease_ns;last_tick=now;}
 void renew(uint64_t id,uint64_t n,uint64_t now){if(!armed||id!=run||n<=seq||now>=deadline)throw std::runtime_error("stale replay lease");seq=n;deadline=now+lease_ns;}
 void finish(uint64_t id){if(!armed||id!=run||!selected||cursor+1!=plan->frames.size())throw std::runtime_error("final frame not selected");finish_requested=true;}
 void cancel_to_planner(uint64_t id){if(!armed||id!=run||terminal)throw std::runtime_error("inactive replay");if(operator_return||finish_requested)return;operator_return=true;return_weight=(phase=="loaded"||phase=="warming")?0.:1.;}
 void release(uint64_t id){if(!armed||id!=run||!return_ready)throw std::runtime_error("Planner handoff not ready");armed=false;completed=true;phase="planner";reason="completed";}
 void stop(const std::string& why){if(armed){armed=false;terminal=true;reason=why;}}
 void watch(uint64_t now){if(armed&&(now>=deadline||now-last_tick>=lease_ns))stop("executor_timeout");}
 Tick tick(uint64_t now,bool admissible,const Feedback& feedback={}){
  watch(now);Tick t;t.stop=terminal;t.run=armed?run:0;if(!armed)return t;
  if(!admissible){stop("native_admission_lost");t.stop=true;return t;}
  tick_gap_ns=now-last_tick;
  if(play_start&&tick_gap_ns>50000000){overrun_limit="callback_gap";stop("schedule_overrun");t.stop=true;return t;}last_tick=now;
  if(!begun){begun=true;start=now;t.begin=true;}const auto elapsed=now-start;
  if(operator_return||(finish_requested&&play_start&&now-play_start-schedule_shift_ns>=plan->frames.back().due+plan->period)){
   if(!return_start){return_start=now;stable_since=0;}
   t.returning=true;t.token=operator_return?plan->frames[cursor].token:plan->frames.back().token;t.frame=cursor;
   t.planner_blend=std::min(1.,double(now-return_start)/blend_ns);
   t.planner_blend=t.planner_blend*t.planner_blend*(3.-2.*t.planner_blend);
   t.planner_blend=1.-return_weight*(1.-t.planner_blend);
   t.external=now-return_start<blend_ns;phase=t.external?"returning":"settling_planner";
   Plan upright;upright.start_gravity={0,0,-1};
   if(!t.external&&aligned(upright,feedback)){if(!stable_since)stable_since=now;}
   else {stable_since=0;return_ready=0;}
   if(stable_since&&now-stable_since>=settle_ns&&!return_ready)return_ready=now;
   if(now-return_start>=entry_timeout_ns){stop("planner_return_timeout");t.stop=true;}
   return t;
  }
  if(elapsed<warm_ns){phase="warming";return t;}
  t.external=true;t.token=plan->frames.front().token;
  if(elapsed<warm_ns+blend_ns){phase="transitioning";t.blend=double(elapsed-warm_ns)/blend_ns;return t;}
  t.blend=1;
  if(!play_start){
   phase="aligning";
   if(aligned(*plan,feedback)){if(!stable_since)stable_since=now;}else stable_since=0;
   if(stable_since&&now-stable_since>=settle_ns){play_start=now;phase="playing";}
   else {if(elapsed>=entry_timeout_ns){stop("start_pose_transition_timeout");t.stop=true;}return t;}
  }
  const auto at=now-play_start-schedule_shift_ns;
  if(at>=plan->frames.back().due+plan->period+100000000){stop("final_frame_unconfirmed");t.stop=true;return t;}
  size_t next=cursor;if(selected&&next+1<plan->frames.size()&&plan->frames[next+1].due<=at+plan->period/2)++next;
  // Assign small early wakeups to the nearest nominal 50 Hz tick. Waiting
  // for exact equality can repeat a frame and retain a full-period backlog.
  // Advance at most one source frame; never skip or batch overdue tokens.
  schedule_frame=next;
  schedule_lateness_ns=at>plan->frames[next].due?at-plan->frames[next].due:0;
  if((!selected||next!=cursor)&&schedule_lateness_ns>30000000){overrun_limit="step_lateness";stop("schedule_overrun");t.stop=true;return t;}
  if((!selected||next!=cursor)&&schedule_lateness_ns>plan->period/2){
   shift_window_ns=0;for(const auto& correction:corrections)if(correction.first&&now-correction.first<1000000000)shift_window_ns+=correction.second;
   // Keep the per-step 30ms bound; also reject sustained slowdown (50ms/s)
   // and unbounded replay stretching (100ms over the entire run).
   if(shift_window_ns+schedule_lateness_ns>50000000||schedule_shift_ns+schedule_lateness_ns>100000000){overrun_limit=shift_window_ns+schedule_lateness_ns>50000000?"window_shift":"total_shift";stop("schedule_overrun");t.stop=true;return t;}
   corrections[rebase_count%corrections.size()]={now,schedule_lateness_ns};
   shift_window_ns+=schedule_lateness_ns;schedule_shift_ns+=schedule_lateness_ns;++rebase_count;
  }
  selected=true;cursor=next;t.frame=cursor;t.token=plan->frames[cursor].token;t.consumed=true;return t;
 }
 Json status()const{
  Json history=Json::array();size_t n=std::min(recent_count,recent.size());
  for(size_t i=recent_count-(terminal?n:0);i<recent_count;++i){const auto& x=recent[i%recent.size()];history.push_back({{"at_ns",x.at},{"callback_gap_ns",x.gap},{"thread_cpu_delta_ns",x.cpu},{"candidate_frame",x.frame},{"lateness_ns",x.late},{"shift_ns",x.shift},{"sched_sample_ns",x.sample},{"sched_wait_total_ns",x.wait},{"sched_runtime_total_ns",x.runtime}});}
  return {{"ok",true},{"entry_protocol",3},{"run",run},{"state",completed?"completed":terminal?"stopping":armed?(play_start?"running":"starting"):"loaded"},{"phase",phase},{"reason",reason},{"return_ready_ns",return_ready},{"schedule",{{"tick_gap_ns",tick_gap_ns},{"lateness_ns",schedule_lateness_ns},{"candidate_frame",schedule_frame},{"shift_ns",schedule_shift_ns},{"rebases",rebase_count},{"limit",overrun_limit},{"command_deferrals",command_deferrals},{"snapshot_deferrals",snapshot_deferrals},{"recent",history}}}};
 }

};
// Only the control callback mutates Core. Management may delay command delivery
// or observation, but cannot suppress a control tick. JSON/socket work stays here.
class Exchange {
 friend struct ExchangeTest;
public:
 struct Command {
  std::string action;std::shared_ptr<const Plan> plan;uint64_t run=0,sequence=0;
  // 0 queued, 1 claimed, 2 done, 3 cancelled. Timeout cancels only unclaimed work.
  std::atomic<int> state{0};Core reply;std::string error;
 };
private:
 Core core_,snapshot_,terminal_snapshot_;std::mutex inbox_mutex_,snapshot_mutex_;
 std::shared_ptr<Command> pending_;std::atomic<bool> terminal_ready_{false},expired_{false};
 std::atomic<uint64_t> watchdog_due_{0};uint64_t last_cpu_=0;
 std::atomic<pid_t> control_tid_{0};std::atomic<uint64_t> sample_version_{0},sample_ns_{0},wait_ns_{0},runtime_ns_{0};
 uint64_t last_sample_attempt_=0;
 void publish(){
  if(core_.terminal&&!terminal_ready_.load()){terminal_snapshot_=core_;terminal_ready_.store(true,std::memory_order_release);}
  std::unique_lock<std::mutex> l(snapshot_mutex_,std::try_to_lock);
  if(l.owns_lock())snapshot_=core_;else ++core_.snapshot_deferrals;
 }
public:
 void submit(const std::shared_ptr<Command>& c){std::lock_guard<std::mutex> l(inbox_mutex_);if(pending_)throw std::runtime_error("replay mailbox busy");pending_=c;}
 Core snapshot(){
  if(terminal_ready_.load(std::memory_order_acquire))return terminal_snapshot_;
  Core copy;{std::lock_guard<std::mutex> l(snapshot_mutex_);copy=snapshot_;}
  if(expired_.load())copy.stop("executor_timeout");
  return copy;
 }
 bool stopping()const{return expired_.load()||terminal_ready_.load();}
 // Management thread only. Never read /proc from the motor callback. These
 // cumulative Linux schedstat counters explicitly measure runnable queue wait;
 // zero/absent samples are not proof of no scheduling delay.
 void sample_scheduler(uint64_t now){
  if(now-last_sample_attempt_<20000000)return;
  last_sample_attempt_=now;
  auto tid=control_tid_.load();if(!tid)return;
  std::string path="/proc/self/task/"+std::to_string(tid)+"/schedstat";
  int fd=open(path.c_str(),O_RDONLY|O_CLOEXEC);if(fd<0)return;
  char bytes[160]{};auto n=read(fd,bytes,sizeof(bytes)-1);close(fd);if(n<=0)return;
  unsigned long long runtime=0,wait=0,slices=0;if(std::sscanf(bytes,"%llu %llu %llu",&runtime,&wait,&slices)!=3)return;
  ++sample_version_;runtime_ns_=runtime;wait_ns_=wait;sample_ns_=now;++sample_version_;
 }
 void watch(uint64_t now){auto due=watchdog_due_.load();if(due&&now>=due)expired_=true;}
 Tick tick(uint64_t now,bool admissible,const Feedback& feedback={}){
  if(!control_tid_.load())control_tid_=pid_t(syscall(SYS_gettid));
  timespec ts{};clock_gettime(CLOCK_THREAD_CPUTIME_ID,&ts);uint64_t cpu=uint64_t(ts.tv_sec)*1000000000+ts.tv_nsec;
  uint64_t cpu_delta=last_cpu_?cpu-last_cpu_:0;last_cpu_=cpu;
  if(expired_)core_.stop("executor_timeout");
  std::shared_ptr<Command> cmd;
  {std::unique_lock<std::mutex> l(inbox_mutex_,std::try_to_lock);if(l.owns_lock())cmd=std::move(pending_);else ++core_.command_deferrals;}
  if(cmd){
   int queued=0;
   if(cmd->state.compare_exchange_strong(queued,1)){
    try{
     core_.watch(now);
     if(cmd->action=="load")core_.load(cmd->plan);
     else if(cmd->action=="start")core_.arm(cmd->run,now);
     else if(cmd->action=="renew"&&!core_.terminal)core_.renew(cmd->run,cmd->sequence,now);
     else if(cmd->action=="finish")core_.finish(cmd->run);
     else if(cmd->action=="cancel_to_planner")core_.cancel_to_planner(cmd->run);
     else if(cmd->action=="release")core_.release(cmd->run);
     else if(cmd->action=="stop"){if(cmd->run!=core_.run)throw std::runtime_error("wrong run");core_.stop("operator_stop");}
     else if(cmd->action!="renew")throw std::runtime_error("unknown replay command");
    }catch(const std::exception& e){cmd->error=e.what();}
   }else cmd.reset();
  }
  core_.callback_cpu_ns=cpu_delta;
  auto version=sample_version_.load();auto sampled=sample_ns_.load(),wait=wait_ns_.load(),runtime=runtime_ns_.load();
  if(!(version&1)&&version==sample_version_.load()){core_.sched_sample_ns=sampled;core_.sched_wait_ns=wait;core_.sched_cpu_ns=runtime;}
  auto result=core_.tick(now,admissible,feedback);
  if(core_.armed||core_.terminal)core_.record_timing(now);
  watchdog_due_=core_.armed?std::min(core_.deadline,core_.last_tick+lease_ns):0;
  publish();
  if(cmd){cmd->reply=core_;cmd->state.store(2,std::memory_order_release);}
  return result;
 }
};
class Service {
 Exchange exchange_;Json artifacts_;std::string path_;int listener_=-1;std::thread thread_;std::atomic<bool> closed_{false};
 static void transfer(int fd,char* p,size_t n,bool write){while(n){auto k=write?send(fd,p,n,MSG_NOSIGNAL):recv(fd,p,n,0);if(k<=0)throw std::runtime_error("replay transport failed");p+=k;n-=size_t(k);}}
 void serve(){
  while(!closed_){
   exchange_.watch(sonic_consumption::mono());exchange_.sample_scheduler(sonic_consumption::mono());
   pollfd poller{listener_,POLLIN,0};if(poll(&poller,1,10)<=0)continue;int fd=accept4(listener_,nullptr,nullptr,SOCK_CLOEXEC);if(fd<0)continue;
   try{
    timeval timeout{0,100000};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
    ucred peer{};socklen_t size=sizeof(peer);if(getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&size)||peer.uid!=getuid()||peer.pid!=getppid())throw std::runtime_error("replay peer is not runtime parent");
    uint64_t length=0;transfer(fd,reinterpret_cast<char*>(&length),8,false);if(!length||length>std::string().max_size())throw std::runtime_error("invalid replay size");
    std::string bytes(length,'\0');transfer(fd,bytes.data(),length,false);auto j=Json::parse(bytes);auto action=j.at("action").get<std::string>();std::shared_ptr<const Plan> plan;
    if(action=="load"){
     for(const auto& item:artifacts_.items())if(item.key()!="native_executable"&&j.at("artifacts").at(item.key())!=item.value())throw std::runtime_error("loaded model/config mismatch");
     plan=parse(j);
    }
    Json reply;
    if(action=="status")reply=exchange_.snapshot().status();
    else {
     auto cmd=std::make_shared<Exchange::Command>();cmd->action=action;cmd->plan=plan;
     if(action!="load")cmd->run=number(j.at("run"));
     if(action=="renew")cmd->sequence=number(j.at("sequence"));
     exchange_.submit(cmd);auto until=sonic_consumption::mono()+100000000;
     while(cmd->state.load(std::memory_order_acquire)!=2){
      exchange_.watch(sonic_consumption::mono());exchange_.sample_scheduler(sonic_consumption::mono());
      if(closed_||sonic_consumption::mono()>=until){int queued=0;cmd->state.compare_exchange_strong(queued,3);throw std::runtime_error("replay command confirmation timeout");}
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
     }
     if(!cmd->error.empty())throw std::runtime_error(cmd->error);
     reply=cmd->reply.status();
    }
    auto out=reply.dump();length=out.size();transfer(fd,reinterpret_cast<char*>(&length),8,true);transfer(fd,out.data(),out.size(),true);
   }catch(...){/* No request can renew a lease on parse/auth failure. */}
   close(fd);
  }
 }
public:
 Service(std::string path,Json artifacts):artifacts_(std::move(artifacts)),path_(std::move(path)){
  auto slash=path_.find_last_of('/');struct stat st{};
  if(slash==std::string::npos||path_.size()>=sizeof(sockaddr_un{}.sun_path)||stat(path_.substr(0,slash).c_str(),&st)||st.st_uid!=getuid()||(st.st_mode&0077))throw std::runtime_error("private replay directory required");
  // A dead previous child's socket is removed by the runtime before launch.
  listener_=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);sockaddr_un addr{};addr.sun_family=AF_UNIX;std::strncpy(addr.sun_path,path_.c_str(),sizeof(addr.sun_path)-1);
  if(listener_<0||bind(listener_,reinterpret_cast<sockaddr*>(&addr),sizeof(addr))||chmod(path_.c_str(),0600)||listen(listener_,4)){if(listener_>=0)close(listener_);throw std::runtime_error("replay bind failed");}
  thread_=std::thread([this]{serve();});
 }
 ~Service(){closed_=true;if(thread_.joinable())thread_.join();close(listener_);unlink(path_.c_str());}
 Json status(){return exchange_.snapshot().status();}
 bool stopping()const{return exchange_.stopping();}
 Tick tick(uint64_t now,bool admissible,const Feedback& feedback={}){return exchange_.tick(now,admissible,feedback);}
};
}
