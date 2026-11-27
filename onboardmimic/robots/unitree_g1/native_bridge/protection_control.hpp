#pragma once
// Explicit, local-only experimental control plane. No controller is armed by
// opening this endpoint. Server must serialize handle() with the actual writer.
#include "protection_roll.hpp"
#include <nlohmann/json.hpp>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <poll.h>
#include <chrono>
#include <fstream>
#include <filesystem>
#include <stdexcept>
namespace sonic_bridge {
struct ProtectionControl {
 using Json=nlohmann::json;
 static uint64_t now_ns(){return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
 Json query_last_failure=nullptr;uint64_t query_count=0,query_failures=0;
 Json query_diagnostics()const{return {{"requests",query_count},{"failures",query_failures},{"last_failure",query_last_failure}};}
 struct Pending {int fd=-1;uint64_t accepted_ns=0;};
 std::array<Pending,4> pending{};
 int listener=-1,pidfd=-1;std::string path;pid_t owner=0;
 explicit ProtectionControl(const std::string& name):path(name){
  auto parent=std::filesystem::path(path).parent_path();struct stat s{};
  if(path.size()>=sizeof(sockaddr_un{}.sun_path)||stat(parent.c_str(),&s)||s.st_uid!=getuid()||(s.st_mode&0077))throw std::runtime_error("protection socket requires private owner directory");
  listener=socket(AF_UNIX,SOCK_SEQPACKET|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
  if(listener<0)throw std::runtime_error("protection socket creation failed");
  sockaddr_un addr{};addr.sun_family=AF_UNIX;std::strncpy(addr.sun_path,path.c_str(),sizeof(addr.sun_path)-1);
  if(bind(listener,reinterpret_cast<sockaddr*>(&addr),sizeof(addr))||chmod(path.c_str(),0600)||listen(listener,4)){
   close(listener);listener=-1;throw std::runtime_error("protection socket bind failed; existing endpoint is never replaced");
  }
 }
 ~ProtectionControl(){for(auto& p:pending)if(p.fd>=0)close(p.fd);if(pidfd>=0)close(pidfd);if(listener>=0){close(listener);unlink(path.c_str());}}
 bool exited()const {if(pidfd<0)return false;pollfd p{pidfd,POLLIN,0};return poll(&p,1,0)>0&&(p.revents&(POLLIN|POLLHUP|POLLERR));}
 Json snapshot(const ProtectedWriter& w)const {
  return {{"protocol",1},{"fault_isolation_scope","profiled_single_joint_faults"},{"disabled_motor_faults",w.disabled_faults},{"disabled_feedback_joints",w.disabled_feedback},{"fault_profile_revision",fault_profiles::revision},{"standing_fold_fault_joints",fault_profiles::standing_fold},{"generation",w.generation},{"stream",w.wbc.stream},{"armed",w.armed},{"watching",w.watching},{"takeover_requested",w.takeover_requested},{"strategy",w.roll.strategy},{"speed_brake_events",w.roll.speed_brake_events},{"braking_joints",std::count(w.roll.braking.begin(),w.roll.braking.end(),true)},{"entry_max_speed_rad_s",w.roll.entry_max_speed},{"lower_foot_estimate",w.roll.lower_foot},{"foot_height_difference_m",w.roll.foot_height_difference},{"fenced",w.fenced},{"estopped",w.estopped},{"phase",int(w.roll.phase)},{"reason",w.roll.reason},{"wbc_pid",owner},{"experimental",true},{"adaptive_ankle_enabled",bool(roll_parameters::adaptive_ankle_enabled)},{"adaptive_ankle_used",w.roll.adaptive_bias_used},{"ankle_bias_rad",w.roll.ankle_bias},{"selected_pitch_rate_rad_s",w.roll.selected_pitch_rate}};
 }
 Json handle(const Json& j,const ucred& peer,ProtectedWriter& w,const RollFeedback& f,double now){
  if(peer.uid!=getuid()||j.at("protocol")!=1)throw std::runtime_error("unauthorized protection request");
  auto action=j.at("action").get<std::string>();
  if(action=="status")return snapshot(w);
  if(action=="reset"){
   // Only the runtime that owns this session may reset it. A changed runtime
   // must explicitly cancel, then reset a terminal session.
   if(w.watching||w.armed||w.roll.phase==RollPhase::running||(w.wbc.state==State::active&&!w.fenced))throw std::runtime_error("active writer cannot reset");
   if(!w.reset(w.generation+1,now))throw std::runtime_error("reset refused");
   if(pidfd>=0)close(pidfd);
   pidfd=-1;owner=0;
  } else if(action=="arm"){
   auto generation=j.at("generation").get<uint64_t>();auto stream=j.at("stream").get<uint64_t>();
   pid_t pid=j.at("pid").get<int>();
   // The requester may only arm its own direct child, never an unrelated PID.
   std::ifstream proc("/proc/"+std::to_string(pid)+"/status");std::string line;pid_t parent=-1;
   while(std::getline(proc,line))if(line.rfind("PPid:",0)==0)parent=std::stoi(line.substr(5));
   if(pid<=1||parent!=peer.pid)throw std::runtime_error("WBC is not the requesting runtime's child");
   int fd=syscall(SYS_pidfd_open,pid,0);if(fd<0)throw std::runtime_error("pidfd required for process-exit protection");
   pollfd p{fd,POLLIN,0};
   if(poll(&p,1,0)!=0||!w.arm(generation,stream,f,now)){close(fd);throw std::runtime_error("protection arming rejected");}
   if(pidfd>=0)close(pidfd);
   pidfd=fd;owner=pid;
  } else if(action=="cancel"){
   if(j.at("generation").get<uint64_t>()!=w.generation)throw std::runtime_error("stale cancellation generation");
   w.cancel(w.generation);
  } else throw std::runtime_error("unknown protection action");
  return snapshot(w);
 }
 // Accept/receive outside the motor lock. Handler alone acquires that lock;
 // no filesystem lookup, PID opening or JSON parsing belongs in the TX loop.
 template<class Handle> void serve_once(Handle handle){
  // A connect() can complete before its client is scheduled to send(). Keep
  // a bounded set of peers across ticks; never wait under the motor lock or
  // block bridge diagnostics for the request arrival deadline.
  int fd=accept4(listener,nullptr,nullptr,SOCK_NONBLOCK|SOCK_CLOEXEC);
  if(fd>=0){
   auto slot=std::find_if(pending.begin(),pending.end(),[](const auto& p){return p.fd<0;});
   if(slot==pending.end()){++query_failures;query_last_failure={{"at_ns",now_ns()},{"stage","accept"},{"error","pending_capacity"}};close(fd);}
   else {*slot={fd,now_ns()};}
  }
  for(auto& entry:pending){
   if(entry.fd<0)continue;
   pollfd ready{entry.fd,POLLIN,0};int available=poll(&ready,1,0);
   if(available==0&&now_ns()-entry.accepted_ns<80000000ULL)continue;
   if(available<0&&errno==EINTR)continue;
   fd=entry.fd;entry.fd=-1;
   Json response;Json diagnostic={{"accepted_ns",entry.accepted_ns},{"handling_ns",now_ns()},{"stage","peer_identity"}};
   ++query_count;
   try{
    ucred peer{};socklen_t size=sizeof(peer);if(getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&peer,&size)||peer.uid!=getuid())throw std::runtime_error("unauthorized peer");
    diagnostic["peer_pid"]=peer.pid;diagnostic["stage"]="wait_request";
    if(available<=0||!(ready.revents&POLLIN))throw std::runtime_error("request deadline");
    diagnostic["stage"]="receive";
    std::array<char,2048> buf{};auto n=recv(fd,buf.data(),buf.size(),MSG_TRUNC);
    if(n<=0||size_t(n)>=buf.size())throw std::runtime_error("invalid request size");
    diagnostic["received_ns"]=now_ns();diagnostic["stage"]="parse";
    auto request=Json::parse(buf.data(),buf.data()+n);
    if(request.contains("diagnostic_id")&&request["diagnostic_id"].is_string()&&request["diagnostic_id"].get<std::string>().size()<=64)diagnostic["id"]=request["diagnostic_id"];
    auto action=request.value("action",std::string{});
    if(action=="status"||action=="arm"||action=="cancel"||action=="reset")diagnostic["action"]=action;
    diagnostic["stage"]="handle";response=handle(request,peer);response["ok"]=true;
    diagnostic["handled_ns"]=now_ns();
    response["query_diagnostics"]=query_diagnostics();
   }catch(const std::exception& e){response={{"ok",false},{"error",e.what()}};diagnostic["error"]=e.what();}
   auto encoded=response.dump();diagnostic["send_started_ns"]=now_ns();
   auto sent=send(fd,encoded.data(),encoded.size(),MSG_NOSIGNAL|MSG_DONTWAIT);int send_errno=errno;
   diagnostic["finished_ns"]=now_ns();
   if(sent!=ssize_t(encoded.size())){diagnostic["stage"]="send_response";diagnostic["send_errno"]=send_errno;diagnostic["send_failed"]=true;}
   if(diagnostic.contains("error")||diagnostic.contains("send_failed")){++query_failures;query_last_failure=std::move(diagnostic);}
   close(fd);
  }
 }
};
}
