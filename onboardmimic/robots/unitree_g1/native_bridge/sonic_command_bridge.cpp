#include "core.hpp"
#include "authority.hpp"
#include "owner_lock.hpp"
#include "compat_owner_lock.hpp"
#include "support.hpp"
#include <rclcpp/rclcpp.hpp>
#include <unitree_hg/msg/low_cmd.hpp>
#include <nlohmann/json.hpp>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/ioctl.h>
#include <poll.h>
#include <mutex>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iostream>
using J=nlohmann::json;
struct Sample {
 uint64_t at=0,seq=0,stream=0,send_ns=0,wall=0,cpu=0,lock=0,gap=0,valid=0;
 int kind=0,state=0,result=0,queued=0; // kind: 0 receive, 1 DDS write
};
J json(const Sample& x){return {{"at_ns",x.at},{"kind",x.kind},{"stream",x.stream},{"seq",x.seq},{"send_ns",x.send_ns},{"wall_ns",x.wall},{"cpu_ns",x.cpu},{"lock_ns",x.lock},{"gap_ns",x.gap},{"valid_ns",x.valid},{"state",sonic_bridge::name(sonic_bridge::State(x.state))},{"result",x.result},{"queued_bytes",x.queued}};}
struct Shared {
 std::mutex mutex;sonic_bridge::Controller controller;
 std::array<float,sonic_bridge::motors> q{};double q_at=-1;
};
struct Options {int udp=16663,cmd=16661,primary=16665,standby=16666;bool physical=false;std::string logs;};
int main(int argc,char** argv){try{
 Options o;
 for(int i=1;i<argc;++i){std::string a=argv[i];
  if(a=="--physical")o.physical=true;
  else if(i+1<argc&&a=="--udp-port")o.udp=std::stoi(argv[++i]);
  else if(i+1<argc&&a=="--cmd-port")o.cmd=std::stoi(argv[++i]);
  else if(i+1<argc&&a=="--primary-port")o.primary=std::stoi(argv[++i]);
  else if(i+1<argc&&a=="--standby-port")o.standby=std::stoi(argv[++i]);
  else if(i+1<argc&&a=="--logs")o.logs=argv[++i];else throw std::runtime_error("invalid arguments");
 }
 for(int port:{o.udp,o.cmd,o.primary,o.standby})sonic_bridge::endpoint(port);
 if(o.logs.empty())throw std::runtime_error("--logs required");
 if(!o.physical){
  std::string domain=getenv("ROS_DOMAIN_ID")?getenv("ROS_DOMAIN_ID"):"";
  std::string xml=getenv("CYCLONEDDS_URI")?getenv("CYCLONEDDS_URI"):"";
  if(domain!="94"||xml.find("<NetworkInterfaceAddress>lo</NetworkInterfaceAddress>")==std::string::npos||o.udp==16663||o.cmd==16661)
   throw std::runtime_error("diagnostic mode requires domain94, inline lo DDS XML and isolated ports");
 }
 sonic_bridge::CompatibleOwnerLock owner(
   o.physical?"/tmp/onboardmimic-motor-control.lock":"/tmp/onboardmimic-diagnostic-domain94.lock",
   o.physical?sonic_bridge::previous_owner_lock():"/tmp/onboardmimic-diagnostic-compat-domain94.lock");
 std::string run=o.logs+"/"+std::to_string(time(nullptr))+"-"+std::to_string(getpid());
 std::filesystem::create_directories(run);
 std::ofstream events(run+"/events.jsonl"),windows(run+"/windows.jsonl");
 if(!events||!windows)throw std::runtime_error("cannot open log files");
 rclcpp::init(0,nullptr);auto node=std::make_shared<rclcpp::Node>("sonic_native_command_bridge");
 auto pub=node->create_publisher<unitree_hg::msg::LowCmd>(o.physical?"/lowcmd":"/onboardmimic/diagnostics/bridge_latency",rclcpp::QoS(rclcpp::KeepLast(1)).best_effort());
 using Authority=sonic_bridge::WriterAuthority<RMW_GID_STORAGE_SIZE>;
 Authority::Gid own{};auto publisher_gid=pub->get_gid();std::copy(std::begin(publisher_gid.data),std::end(publisher_gid.data),own.begin());
 if(!publisher_gid.implementation_identifier||std::string(publisher_gid.implementation_identifier)!="rmw_cyclonedds_cpp")
  throw std::runtime_error("Authority monitor requires RMW_IMPLEMENTATION=rmw_cyclonedds_cpp");
 std::mutex authority_mutex;Authority authority(own,sonic_bridge::ns()/1e9);
 std::atomic<bool> observer_fault{false};
 // Observe the serialized envelope and sender ID without parsing motors.
 // Cyclone DDS is required above: other ROS backends have failed to observe
 // SDK vendor traffic on the actual robot even while ROS-only tests passed.
 auto monitor=node->create_subscription<unitree_hg::msg::LowCmd>(
   o.physical?"/lowcmd":"/onboardmimic/diagnostics/bridge_latency",rclcpp::QoS(rclcpp::KeepLast(256)).best_effort(),
   [](unitree_hg::msg::LowCmd::ConstSharedPtr){});
 rclcpp::SerializedMessage raw(4096);rclcpp::MessageInfo info;
 auto observe_commands=[&]{
  for(int i=0;i<256;++i){
   if(!monitor->take_serialized(raw,info))return;
   Authority::Gid sender{};const auto& gid=info.get_rmw_message_info().publisher_gid;
   std::copy(std::begin(gid.data),std::end(gid.data),sender.begin());
   std::lock_guard<std::mutex> lock(authority_mutex);authority.observe(sender);
   if(authority.conflict)return;
  }
  // Before admission, incomplete observation rejects startup. After admission,
  // monitoring is advisory and never interrupts the incumbent writer.
  throw std::runtime_error("LowCmd observer backlog exceeded");
 };
 // No motor command is sent during discovery. Registered but silent vendor
 // endpoints are allowed; any actual foreign command fails closed.
 while(rclcpp::ok()){
  observe_commands();
  const auto now=sonic_bridge::ns()/1e9;
  if(authority.conflict){
   events<<J({{"event","authority_startup_conflict"},{"dds_writes",0}}).dump()<<std::endl;
   throw std::runtime_error("CONTROL_AUTHORITY_CONFLICT: another LowCmd writer is active; release its control before connecting SONIC");
  }
  if(authority.acquire(now))break;
  std::this_thread::sleep_for(std::chrono::milliseconds(2));
 }
 if(!rclcpp::ok())return 0;
 int udp=socket(AF_INET,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);if(udp<0)throw std::runtime_error("UDP socket");
 int size=65536;setsockopt(udp,SOL_SOCKET,SO_RCVBUF,&size,sizeof(size));
 sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);address.sin_port=htons(o.udp);
 if(bind(udp,reinterpret_cast<sockaddr*>(&address),sizeof(address)))throw std::runtime_error("UDP bind failed");
 sonic_bridge::Context ctx;Shared shared;sonic_bridge::Ring<Sample> rx_ring,tx_ring;
 std::atomic<bool> ready{false};std::atomic<uint64_t> received{0},accepted{0},writes{0},invalid{0},stale{0},crc_bad{0},ignored{0},q_errors{0},zmq_ignored{0};
 std::atomic<int> rx_tid{0},tx_tid{0};std::exception_ptr rx_error,tx_error,feedback_error;
 std::thread receiver([&]{try{
  rx_tid=syscall(SYS_gettid);std::array<uint8_t,2048> buffer{};uint64_t previous=0;
  while(rclcpp::ok()){
   pollfd p{udp,POLLIN,0};int result=poll(&p,1,5);if(result<0&&errno!=EINTR)throw std::runtime_error("UDP poll failed");if(result<=0)continue;
   // One datagram per iteration, committed immediately after validation. No
   // drain-then-commit batch, and this thread never calls DDS or writes logs.
   sockaddr_in peer{};socklen_t length=sizeof(peer);auto begin=sonic_bridge::ns(),cpu=sonic_bridge::cpu_ns();
   ssize_t n=recvfrom(udp,buffer.data(),buffer.size(),0,reinterpret_cast<sockaddr*>(&peer),&length);
   if(n<0){if(errno==EAGAIN||errno==EINTR)continue;throw std::runtime_error("UDP receive failed");}
   ++received;auto at=sonic_bridge::ns();sonic_bridge::Packet packet;auto status=sonic_bridge::decode(buffer.data(),n,at,packet);
   Sample x;x.at=at;x.seq=packet.seq;x.stream=packet.stream;x.send_ns=packet.send_ns;x.gap=previous?at-previous:0;previous=at;x.result=int(status);
   if(peer.sin_addr.s_addr!=htonl(INADDR_LOOPBACK)){++invalid;x.result=10;}
   else if(status!=sonic_bridge::Parse::ok){++invalid;if(status==sonic_bridge::Parse::crc_error)++crc_bad;if(status==sonic_bridge::Parse::stale)++stale;}
   else {
    auto wait=sonic_bridge::ns();std::lock_guard<std::mutex> lock(shared.mutex);auto now=sonic_bridge::ns();x.lock=now-wait;
    // Freshness includes validation/lock delay; queued packets cannot refresh a watchdog.
    if(now-packet.send_ns>100000000){++invalid;++stale;x.result=int(sonic_bridge::Parse::stale);}
    else if(shared.controller.observe(packet,now/1e9,(now/1e9-shared.q_at>=0&&now/1e9-shared.q_at<=.05)?&shared.q:nullptr)) ++accepted;
    else {++ignored;x.result=11;}
    x.state=int(shared.controller.state);x.valid=shared.controller.last_valid<0?0:uint64_t(shared.controller.last_valid*1e9);
   }
   ioctl(udp,FIONREAD,&x.queued);x.wall=sonic_bridge::ns()-begin;x.cpu=sonic_bridge::cpu_ns()-cpu;rx_ring.push(x);
  }
 }catch(...){rx_error=std::current_exception();rclcpp::shutdown();}});
 std::thread feedback([&]{try{
  // Keep the compatibility port bound, but metadata-less and duplicate ZMQ
  // LowCmds never gain authority. This onboard runtime uses compact UDP only.
  sonic_bridge::Socket compat(ctx,ZMQ_SUB);compat.set(ZMQ_RCVHWM,4);compat.subscribe();compat.bind(o.cmd);
  sonic_bridge::Socket primary(ctx,ZMQ_SUB),standby(ctx,ZMQ_SUB);primary.set(ZMQ_RCVHWM,4);standby.set(ZMQ_RCVHWM,4);primary.subscribe();standby.subscribe();primary.connect(o.primary);standby.connect(o.standby);
  std::array<zmq_pollitem_t,3> items{{{primary.ptr,0,ZMQ_POLLIN,0},{standby.ptr,0,ZMQ_POLLIN,0},{compat.ptr,0,ZMQ_POLLIN,0}}};
  bool have_tick=false;uint32_t last_tick=0;ready=true;
  while(rclcpp::ok()){
   zmq_poll(items.data(),items.size(),5);
   for(size_t i=0;i<items.size();++i)if(items[i].revents&ZMQ_POLLIN){
    zmq_msg_t raw;zmq_msg_init(&raw);int n=zmq_msg_recv(&raw,items[i].socket,ZMQ_DONTWAIT);
    if(n<0){zmq_msg_close(&raw);continue;}
    if(i==2){++zmq_ignored;zmq_msg_close(&raw);continue;}
    try{
     if(n>65536)throw std::runtime_error("oversized feedback");
     auto h=msgpack::unpack(static_cast<char*>(zmq_msg_data(&raw)),n);auto& e=h.get();
     if(sonic_bridge::field(e,"source").as<std::string>()!=(i==0?"primary":"standby"))throw std::runtime_error("source mismatch");
     if(sonic_bridge::field(e,"topic").as<std::string>()=="rt/lowstate"){
      double captured=sonic_bridge::field(e,"captured").as<double>();double now=sonic_bridge::ns()/1e9;
      auto tick=sonic_bridge::field(e,"tick").as<uint32_t>();
      if(std::isfinite(captured)&&now-captured>=0&&now-captured<=.05&&(!have_tick||(uint32_t(tick-last_tick)>0&&uint32_t(tick-last_tick)<0x80000000u))){
       auto& bytes=sonic_bridge::field(e,"payload");if(bytes.type!=msgpack::type::BIN)throw std::runtime_error("payload");
       auto payload=msgpack::unpack(bytes.via.bin.ptr,bytes.via.bin.size);auto& motors=sonic_bridge::field(payload.get(),"motor_state");
       if(motors.type!=msgpack::type::ARRAY||motors.via.array.size!=35)throw std::runtime_error("motor count");
       std::array<float,sonic_bridge::motors> q;
       for(size_t m=0;m<q.size();++m){q[m]=sonic_bridge::field(motors.via.array.ptr[m],"q").as<float>();if(!std::isfinite(q[m]))throw std::runtime_error("nonfinite q");}
       std::lock_guard<std::mutex> lock(shared.mutex);if(captured>shared.q_at){shared.q=q;shared.q_at=captured;last_tick=tick;have_tick=true;}
      }
     }
    }catch(const std::exception&){++q_errors;}
    zmq_msg_close(&raw);
   }
  }
 }catch(...){feedback_error=std::current_exception();rclcpp::shutdown();}});
 std::thread writer([&]{try{
  tx_tid=syscall(SYS_gettid);uint64_t previous=0;auto deadline=std::chrono::steady_clock::now();unitree_hg::msg::LowCmd msg;
  while(rclcpp::ok()){
   deadline+=std::chrono::milliseconds(2);std::this_thread::sleep_until(deadline);
   auto now_clock=std::chrono::steady_clock::now();if(now_clock-deadline>std::chrono::milliseconds(2))deadline=now_clock; // never catch up in bursts
   auto begin=sonic_bridge::ns(),cpu=sonic_bridge::cpu_ns();sonic_bridge::Command command;Sample x;x.kind=1;x.at=begin;bool send=false;
   {
    std::lock_guard<std::mutex> lock(shared.mutex);auto now=sonic_bridge::ns();x.lock=now-begin;shared.controller.advance(now/1e9);
    const auto& c=shared.controller;x.state=int(c.state);x.seq=c.seq;x.stream=c.stream;x.send_ns=c.send_ns;x.valid=c.last_valid<0?0:uint64_t(c.last_valid*1e9);
    if(c.state!=sonic_bridge::State::idle&&ready.load()){command=c.output;send=true;}
   }
   if(send){
    msg.mode_pr=command.mode_pr;msg.mode_machine=command.mode_machine;msg.reserve=command.reserve;msg.crc=command.crc;
    for(size_t i=0;i<35;++i){const auto& m=command.motor[i];auto& d=msg.motor_cmd[i];d.mode=m.mode;d.q=m.q;d.dq=m.dq;d.tau=m.tau;d.kp=m.kp;d.kd=m.kd;d.reserve=m.reserve;}
    // The incumbent keeps its lock for this process lifetime. Observations of
    // later DDS writers do not gate this path or interrupt policy output.
    pub->publish(msg);++writes;x.result=1;x.gap=previous?begin-previous:0;previous=begin;
   }
   x.wall=sonic_bridge::ns()-begin;x.cpu=sonic_bridge::cpu_ns()-cpu;tx_ring.push(x);
  }
 }catch(...){tx_error=std::current_exception();rclcpp::shutdown();}});
 std::thread observer([&]{try{
  while(rclcpp::ok()){
   observe_commands();
   std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
 }catch(...){observer_fault=true;}});
 // Logging owns all file I/O. Fixed SPSC queues drop diagnostics rather than
 // blocking control, and expose the drop count. No stack signals or /proc work
 // occur on the receiver/writer threads.
 std::string boot_id;std::ifstream("/proc/sys/kernel/random/boot_id")>>boot_id;
 J start={{"boot_id",boot_id},{"unix_s",time(nullptr)},{"monotonic_ns",sonic_bridge::ns()},{"event","native_bridge_start"},{"pid",getpid()},{"physical",o.physical},{"watchdog_ms",100},{"failsafe_ramp_ms",150},{"recovery_ramp_ms",500},{"udp_port",o.udp},{"log_dir",run}};
 events<<start.dump()<<'\n';std::cout<<start.dump()<<std::endl;
 std::deque<Sample> history;uint64_t last_log=0,window_until=0,last_trigger=0;std::array<uint64_t,2> max_gap{},max_wall{},max_lock{},max_valid_age{};int last_state=-1;bool intrusion_reported=false,observer_error_reported=false;
 while(rclcpp::ok()){
  uint64_t now=sonic_bridge::ns();Sample x;
  for(auto* ring:{&rx_ring,&tx_ring})while(ring->pop(x)){
   size_t k=x.kind;max_gap[k]=std::max(max_gap[k],x.gap);max_wall[k]=std::max(max_wall[k],x.wall);max_lock[k]=std::max(max_lock[k],x.lock);
   if(x.valid&&x.at>x.valid)max_valid_age[k]=std::max(max_valid_age[k],x.at-x.valid);
   bool change=x.kind==1&&last_state!=x.state;
   if(change){events<<J({{"event","watchdog_transition"},{"sample",json(x)}}).dump()<<'\n';last_state=x.state;}
   bool anomaly=x.gap>20000000||x.wall>20000000||x.lock>20000000;
   if(anomaly)events<<J({{"event","bridge_delay"},{"sample",json(x)}}).dump()<<'\n';
   if((anomaly||(change&&x.state==int(sonic_bridge::State::failing)))&&now-last_trigger>10000000000ULL){
    last_trigger=now;window_until=now+2000000000;
    for(const auto& old:history)windows<<json(old).dump()<<'\n';
    J sched={{"event","scheduler_snapshot"},{"at_ns",now}};
    for(auto id:{rx_tid.load(),tx_tid.load()}){std::ifstream f("/proc/self/task/"+std::to_string(id)+"/schedstat");std::string s;std::getline(f,s);sched[std::to_string(id)]=s;}events<<sched.dump()<<'\n';
   }
   if(now<window_until)windows<<json(x).dump()<<'\n';
   history.push_back(x);
   // Receive and writer queues are drained independently; keep a bounded
   // recent history by count, not an assumed globally sorted timestamp.
   while(history.size()>4000)history.pop_front();
  }
  if(now-last_log>=1000000000){
   uint64_t foreign_count=0;Authority::Gid foreign_gid{};
   {std::lock_guard<std::mutex> lock(authority_mutex);foreign_count=authority.foreign_count;foreign_gid=authority.last_foreign;}
   if(foreign_count&&!intrusion_reported){
    events<<J({{"event","foreign_lowcmd_observed"},{"action","keep_current_owner"},{"foreign_gid",foreign_gid}}).dump()<<'\n';intrusion_reported=true;
   }
   if(observer_fault&&!observer_error_reported){
    events<<J({{"event","authority_observer_error"},{"action","keep_current_owner"}}).dump()<<'\n';observer_error_reported=true;
   }
   J j={{"event","native_bridge_stats"},{"at_ns",now},{"foreign_lowcmd_count",foreign_count},{"authority_observer_error",observer_fault.load()},{"received",received.load()},{"accepted",accepted.load()},{"dds_writes",writes.load()},{"invalid",invalid.load()},{"crc_errors",crc_bad.load()},{"stale_packets",stale.load()},{"ignored",ignored.load()},{"feedback_errors",q_errors.load()},{"zmq_ignored",zmq_ignored.load()},{"rx_log_drops",rx_ring.dropped.load()},{"tx_log_drops",tx_ring.dropped.load()},{"max_gap_ns",max_gap},{"max_wall_ns",max_wall},{"max_lock_ns",max_lock},{"max_valid_age_ns",max_valid_age},{"state",last_state<0?"idle":sonic_bridge::name(sonic_bridge::State(last_state))}};
   events<<j.dump()<<'\n';events.flush();windows.flush();std::cout<<j.dump()<<std::endl;last_log=now;
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(10));
 }
 receiver.join();writer.join();feedback.join();observer.join();close(udp);
 events<<J({{"event","native_bridge_stop"},{"received",received.load()},{"dds_writes",writes.load()}}).dump()<<std::endl;
 for(auto error:{rx_error,tx_error,feedback_error})if(error)std::rethrow_exception(error);
 return 0;
}catch(const std::exception& e){std::cerr<<"native bridge fatal: "<<e.what()<<std::endl;return 1;}}
