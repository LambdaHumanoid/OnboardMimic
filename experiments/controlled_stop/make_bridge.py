"""Generate an experimental build; production bridge sources stay unchanged."""
from pathlib import Path
import shutil
root=Path(__file__).resolve().parent;repo=root.parents[1]
src=repo/'onboardmimic/robots/unitree_g1/native_bridge';out=root/'bridge_build_source';out.mkdir(exist_ok=True)
for p in src.glob('*'):
    if p.suffix in ('.hpp','.cpp') or p.name=='CMakeLists.txt':shutil.copy2(p,out/p.name)
shutil.copy2(root/'handoff.hpp',out/'handoff.hpp')
p=out/'sonic_command_bridge.cpp';s=p.read_text().replace('#include "core.hpp"','#include "handoff.hpp"\n#include <sys/un.h>')
s=s.replace('sonic_bridge::Controller controller;', 'sonic_bridge::ExperimentController controller;')
anchor=' std::thread observer([&]{try{'
assert s.count(anchor)==1
ipc=r'''
 // Private local IPC, available only in this separately built experiment.
 std::thread experiment([&]{
  const char* path=getenv("ONBOARDMIMIC_EXPERIMENT_SOCKET");
  if(!path||strlen(path)>=100)return;
  int listener=socket(AF_UNIX,SOCK_SEQPACKET|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
  if(listener<0)return;
  sockaddr_un addr{};addr.sun_family=AF_UNIX;strncpy(addr.sun_path,path,sizeof(addr.sun_path)-1);
  // Never replace a socket held by another controller.
  if(bind(listener,(sockaddr*)&addr,sizeof(addr))||chmod(path,0600)||listen(listener,4)){close(listener);return;}
  while(rclcpp::ok()){
   pollfd item{listener,POLLIN,0};if(poll(&item,1,20)<=0)continue;
   int fd=accept4(listener,nullptr,nullptr,SOCK_NONBLOCK|SOCK_CLOEXEC);if(fd<0)continue;
   ucred cred{};socklen_t n=sizeof(cred);J response;
   try{
    if(getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&cred,&n)||cred.uid!=geteuid())throw std::runtime_error("peer uid");
    pollfd request{fd,POLLIN,0};if(poll(&request,1,10)<=0)throw std::runtime_error("request timeout");
    char buffer[2048];int size=recv(fd,buffer,sizeof(buffer),0);if(size<=0||size==sizeof(buffer))throw std::runtime_error("request size");
    auto input=J::parse(buffer,buffer+size);std::lock_guard<std::mutex> lock(shared.mutex);
    auto& c=shared.controller;auto now=sonic_bridge::ns()/1e9;auto action=input.at("action").get<std::string>();
    if(action=="claim"){
     if(!c.claim(input.at("expected_stream").get<uint64_t>(),input.at("new_stream").get<uint64_t>(),now))throw std::runtime_error("handoff not eligible");
    }else if(action=="cancel")c.cancel();else if(action!="snapshot")throw std::runtime_error("unsupported action");
    J command=J::array();for(int i=0;i<29;i++){const auto& m=c.output.motor[i];command.push_back({m.q,m.dq,m.tau,m.kp,m.kd});}
    response={{"ok",true},{"state",sonic_bridge::name(c.state)},{"stream",c.stream},{"age",now-c.last_valid},{"command",command},{"at",now}};
   }catch(const std::exception& e){response={{"ok",false},{"error",e.what()}};}
   auto bytes=response.dump();send(fd,bytes.data(),bytes.size(),MSG_NOSIGNAL);close(fd);
  }
  close(listener);unlink(path);
 });
'''
s=s.replace(anchor,ipc+anchor).replace('receiver.join();writer.join();feedback.join();observer.join();close(udp);','receiver.join();writer.join();feedback.join();observer.join();experiment.join();close(udp);')
p.write_text(s)
print(out)
