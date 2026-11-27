#include "protection_control.hpp"
#include <cassert>
#include <csignal>
#include <sys/wait.h>
#include <iostream>
#include <thread>
using namespace sonic_bridge;
void delayed_status_test(){
 char dir[]="/tmp/protection-request-XXXXXX";assert(mkdtemp(dir));
 {
  ProtectionControl control(std::string(dir)+"/control.sock");int handled=0;
  auto handler=[&](const auto& request,const auto&){++handled;assert(request["action"]=="status");return nlohmann::json{{"protocol",1}};};
  auto connect_peer=[&](){int fd=socket(AF_UNIX,SOCK_SEQPACKET,0);assert(fd>=0);sockaddr_un a{};a.sun_family=AF_UNIX;strcpy(a.sun_path,control.path.c_str());assert(connect(fd,(sockaddr*)&a,sizeof(a))==0);return fd;};
  int slow=connect_peer();control.serve_once(handler);assert(handled==0);
  // Simulate a preempted client after connect(); no server wait or early close.
  std::this_thread::sleep_for(std::chrono::milliseconds(20));
  std::string req="{\"protocol\":1,\"action\":\"status\"}";
  assert(send(slow,req.data(),req.size(),MSG_NOSIGNAL)==(ssize_t)req.size());
  control.serve_once(handler);char buffer[512];assert(recv(slow,buffer,sizeof(buffer),MSG_DONTWAIT)>0);close(slow);assert(handled==1);
  int idle=connect_peer();control.serve_once(handler);
  int fast=connect_peer();assert(send(fast,req.data(),req.size(),MSG_NOSIGNAL)>0);control.serve_once(handler);
  assert(recv(fast,buffer,sizeof(buffer),MSG_DONTWAIT)>0);close(fast);assert(handled==2);
  // Expiry is bounded and never calls the command handler for an empty peer.
  std::this_thread::sleep_for(std::chrono::milliseconds(85));control.serve_once(handler);
  assert(send(idle,req.data(),req.size(),MSG_NOSIGNAL)<0);close(idle);assert(handled==2);
  auto diagnostic=control.query_diagnostics();assert(diagnostic["failures"]==1);
  assert(diagnostic["last_failure"]["stage"]=="wait_request");
  assert(diagnostic["last_failure"]["error"]=="request deadline");
  assert(diagnostic["last_failure"]["handling_ns"].get<uint64_t>()>diagnostic["last_failure"]["accepted_ns"].get<uint64_t>());
 }
 rmdir(dir);std::cout<<"Delayed status request, independent peers and bounded expiry passed\n";
}
int main(){
 delayed_status_test();
 int probe=syscall(SYS_pidfd_open,getpid(),0);
 if(probe<0&&errno==ENOSYS){std::cout<<"SKIP: kernel lacks pidfd_open; real process-exit integration not tested\n";return 77;}
 if(probe>=0)close(probe);
 char dir[]="/tmp/protection-control-XXXXXX";assert(mkdtemp(dir));
 {
 ProtectionControl control(std::string(dir)+"/control.sock");ProtectedWriter w;
 ucred peer{};peer.uid=getuid();peer.pid=getpid();
 RollFeedback f;f.valid=true;f.captured=1;f.q[3]=f.q[9]=.6;
 auto call=[&](const nlohmann::json& j){return control.handle(j,peer,w,f,1);};
 auto r=call({{"protocol",1},{"action","reset"}});assert(r["generation"]==1);
 Packet p;p.stream=42;p.seq=1;p.send_ns=1000000000;
 for(size_t i=0;i<29;++i)p.cmd.motor[i]={1,f.q[i],0,0,40,3,0};
 assert(w.observe(p,1,f));
 bool rejected=false;try{call({{"protocol",1},{"action","reset"}});}catch(...){rejected=true;}assert(rejected);
 rejected=false;try{call({{"protocol",1},{"action","arm"},{"generation",1},{"stream",42},{"pid",getpid()}});}catch(...){rejected=true;}assert(rejected);
 pid_t child=fork();assert(child>=0);if(child==0){for(;;)pause();}
 try{
 r=call({{"protocol",1},{"action","arm"},{"generation",1},{"stream",42},{"pid",child}});assert(r["armed"]==true);assert(!control.exited());
 kill(child,SIGTERM);waitpid(child,nullptr,0);assert(control.exited());
 f.captured=1.002;w.advance(f,1.002,control.exited());assert(w.fenced&&w.roll.phase==RollPhase::running);
 rejected=false;try{call({{"protocol",1},{"action","cancel"},{"generation",0}});}catch(...){rejected=true;}assert(rejected&&!w.estopped);
 call({{"protocol",1},{"action","cancel"},{"generation",1}});assert(w.estopped&&w.roll.phase==RollPhase::aborted);
 auto old=peer.uid;peer.uid=old+1;rejected=false;try{call({{"protocol",1},{"action","status"}});}catch(...){rejected=true;}assert(rejected);peer.uid=old;
 }catch(...){kill(child,SIGKILL);waitpid(child,nullptr,0);throw;}
 }
 assert(!std::filesystem::exists(std::string(dir)+"/control.sock"));rmdir(dir);
 std::cout<<"Control plane: owned-child pidfd exit, cancellation, session and peer rejection passed\n";
}
