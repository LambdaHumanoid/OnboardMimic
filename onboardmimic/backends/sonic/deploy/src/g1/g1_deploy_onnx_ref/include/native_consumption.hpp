#pragma once
// Opt-in diagnostics only. Fixed SPSC queues on producers; all JSON/network work
// belongs to a background thread. No motor authority or command mutations.
#include <array>
#include <atomic>
#include <chrono>
#include <time.h>
#include <sys/resource.h>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <filesystem>
#include <type_traits>
#include <memory>
#include <thread>
#include <string>
#include <vector>
#include <stdexcept>
#include <algorithm>
#include <nlohmann/json.hpp>
#include <zmq.h>
#include "../../../TRTInference/picosha2.h"

namespace sonic_consumption {
using Json=nlohmann::json;
inline uint64_t mono(){return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
inline uint64_t thread_cpu(){timespec t{};clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t);return uint64_t(t.tv_sec)*1000000000ULL+t.tv_nsec;}
inline uint64_t wall(){return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::system_clock::now().time_since_epoch()).count();}
inline std::string digest(const std::string& bytes){return picosha2::hash256_hex_string(bytes);}
inline std::string file_digest(const std::string& path){std::ifstream f(path,std::ios::binary);if(!f)throw std::runtime_error("artifact unreadable");std::string s((std::istreambuf_iterator<char>(f)),{});return digest(s);}
inline std::string session(){std::string s;std::ifstream("/proc/sys/kernel/random/uuid")>>s;if(s.empty())throw std::runtime_error("session unavailable");return s;}
constexpr size_t max_input=4096,max_fields=32,max_history=64;
struct History {size_t count=0;std::array<uint64_t,max_history> ns{},index{},low_state_received_ns{},torso_imu_received_ns{};};
struct Record {
 int kind=0,mode=-1; // 0 inference attempt; 1 transport mapping; 2 protected DDS commit
 uint64_t index=0,at=0,realtime=0,finished=0,stream=0,seq=0,send_ns=0,generation=0,reset_ns=0;
 uint64_t replaced_index=0,replaced_generation=0;bool replaced_selected=true;
 uint64_t cpu_started=0,cpu_elapsed=0;std::array<uint64_t,5> infer_timing{};
 // Absolute monotonic stage clocks; zero means the stage was not reached.
 std::array<uint64_t,6> control_stages{};
 // Encoder gather start/end, Encode start/end. Zero means not reached/bypassed.
 std::array<uint64_t,4> encoder_stages{};
 std::array<uint64_t,5> encoder_timing{};
 uint64_t encoder_gather_cpu_ns=0,encoder_infer_cpu_ns=0;
 uint64_t control_cpu_started=0,control_cpu_elapsed=0,command_buffered_ns=0;
 long voluntary_started=0,involuntary_started=0,voluntary_switches=0,involuntary_switches=0;
 bool inference_ok=false,command_ok=false,authority=false;
 uint64_t replay_run=0,replay_frame=0;bool replay_consumed=false;
 size_t input_size=0,encoder_size=0;uint64_t encoder_at=0;
 std::array<float,max_input> input{},encoder_input{};
 std::array<History,max_fields> histories{};
 std::array<float,29> raw_action{},q{},dq{},kp{},kd{},tau{};
};
template<class T,size_t N=16> struct Ring {
 std::array<T,N> slots{};std::atomic<size_t> head{0},tail{0};std::atomic<uint64_t> dropped{0},last_dropped_index{0},last_dropped_sequence{0};
 bool push(const T& value){auto h=head.load(std::memory_order_relaxed);if(h-tail.load(std::memory_order_acquire)>=N){if constexpr(std::is_same_v<T,Record>){last_dropped_index.store(value.index,std::memory_order_relaxed);last_dropped_sequence.store(value.seq,std::memory_order_relaxed);}++dropped;return false;}slots[h%N]=value;head.store(h+1,std::memory_order_release);return true;}
 bool pop(T& value){auto t=tail.load(std::memory_order_relaxed);if(t==head.load(std::memory_order_acquire))return false;value=slots[t%N];tail.store(t+1,std::memory_order_release);return true;}
};
inline Json encode(const Record& r){
 Json j={{"kind",r.kind},{"index",r.index},{"at_ns",r.at},{"realtime_ns",r.realtime},{"finished_ns",r.finished},
 {"stream",r.stream},{"seq",r.seq},{"send_ns",r.send_ns},{"generation",r.generation},{"reset_ns",r.reset_ns},{"authority",r.authority}};
 if(r.kind==0){
  j["timing"]={{"thread_cpu_ns",r.cpu_elapsed},{"enqueue_wall_ns",r.infer_timing[0]},{"synchronize_wall_ns",r.infer_timing[1]}};
  j["timing"]["input_copy_wall_ns"]=r.infer_timing[2];
  j["timing"]["launch_wall_ns"]=r.infer_timing[3];
  j["timing"]["output_copy_wall_ns"]=r.infer_timing[4];
  j["buffer_replacement"]={{"boundary","serialized_writer_selection"},{"index",r.replaced_index},{"generation",r.replaced_generation},{"selected",r.replaced_selected}};
  j["timing"]["encoder_stages_ns"]=r.encoder_stages;
  j["timing"]["encoder_gather_cpu_ns"]=r.encoder_gather_cpu_ns;
  j["timing"]["encoder_infer_cpu_ns"]=r.encoder_infer_cpu_ns;
  j["timing"]["encoder"]={{"enqueue_wall_ns",r.encoder_timing[0]},
    {"synchronize_wall_ns",r.encoder_timing[1]},{"input_copy_wall_ns",r.encoder_timing[2]},
    {"launch_wall_ns",r.encoder_timing[3]},{"output_copy_wall_ns",r.encoder_timing[4]}};
  j["timing"]["control_stages_ns"]=r.control_stages;
  j["timing"]["control_thread_cpu_ns"]=r.control_cpu_elapsed;
  j["timing"]["command_buffered_ns"]=r.command_buffered_ns;
  j["timing"]["voluntary_switches"]=r.voluntary_switches;
  j["timing"]["involuntary_switches"]=r.involuntary_switches;
  j["replay"]={{"run",r.replay_run},{"frame",r.replay_frame},{"scheduled_frame",r.replay_consumed}};
  j["mode"]=r.mode;j["inference_ok"]=r.inference_ok;j["command_ok"]=r.command_ok;
  j["input"]=std::vector<float>(r.input.begin(),r.input.begin()+r.input_size);
  j["encoder_input"]=std::vector<float>(r.encoder_input.begin(),r.encoder_input.begin()+r.encoder_size);j["encoder_at_ns"]=r.encoder_at;
  j["raw_action"]=r.raw_action;j["histories"]=Json::array();
  for(const auto& h:r.histories)j["histories"].push_back({{"ns",std::vector<uint64_t>(h.ns.begin(),h.ns.begin()+h.count)}, {"index",std::vector<uint64_t>(h.index.begin(),h.index.begin()+h.count)}, {"low_state_received_ns",std::vector<uint64_t>(h.low_state_received_ns.begin(),h.low_state_received_ns.begin()+h.count)}, {"torso_imu_received_ns",std::vector<uint64_t>(h.torso_imu_received_ns.begin(),h.torso_imu_received_ns.begin()+h.count)}});
 }
 if(r.kind!=1)j["command"]={{"q",r.q},{"dq",r.dq},{"kp",r.kp},{"kd",r.kd},{"tau",r.tau}};
 return j;
}
// Compact audit is produced by the telemetry publisher, never the control loop.
// A separate bounded SPSC queue isolates filesystem stalls from publication.
struct AuditRecord {
 uint64_t at=0,index=0,stream=0,seq=0,source_ns=0,finished_ns=0,send_ns=0;
 uint64_t inference_drops=0,writer_drops=0,send_errors=0;
 uint64_t last_dropped_index=0,last_dropped_sequence=0,publication_sequence=0;
 int kind=0;bool inference_ok=false,command_ok=false,authority=false;
};
class Audit {
 Ring<AuditRecord,4096> queue_;std::atomic<bool> stop_{false};std::thread worker_;
 std::string path_,session_;bool enabled_;
 void run() noexcept {
  try {
   std::filesystem::create_directories(std::filesystem::path(path_).parent_path());
   std::ofstream out;size_t bytes=0;
   auto open=[&]{out.open(path_,std::ios::app);if(!out)throw std::runtime_error("audit open");bytes=std::filesystem::file_size(path_);};open();
   while(!stop_||queue_.head.load()!=queue_.tail.load()){AuditRecord r;bool wrote=false;
    for(int n=0;n<256&&queue_.pop(r);++n){
     Json j={{"session",session_},{"stage","native_publish_attempt"},{"at_ns",r.at},{"kind",r.kind},{"index",r.index},{"stream",r.stream},{"seq",r.seq},
      {"source_ns",r.source_ns},{"finished_ns",r.finished_ns},{"send_ns",r.send_ns},{"inference_ok",r.inference_ok},{"command_ok",r.command_ok},{"authority",r.authority},
      {"inference_queue_drops",r.inference_drops},{"writer_queue_drops",r.writer_drops},{"send_errors",r.send_errors},{"last_dropped_index",r.last_dropped_index},{"last_dropped_sequence",r.last_dropped_sequence},{"publication_sequence",r.publication_sequence},{"audit_drops",queue_.dropped.load()}};
     auto line=j.dump()+"\n";
     if(bytes+line.size()>8*1024*1024){out.close();std::filesystem::rename(path_,path_+".1");open();}
     out<<line;if(!out)throw std::runtime_error("audit write");bytes+=line.size();wrote=true;
    }
    if(wrote){out.flush();}
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
   }
  }catch(...){failed=true;}
 }
public:
 std::atomic<bool> failed{false};
 Audit(int port,const std::string& session):session_(session),enabled_(!std::getenv("OM_SONIC_AUDIT")||std::string(std::getenv("OM_SONIC_AUDIT"))!="0"){
  const char* dir=std::getenv("OM_SONIC_AUDIT_DIR");path_=std::string(dir?dir:"var/logs")+"/consumption-native-"+std::to_string(port)+".jsonl";
  if(enabled_)worker_=std::thread([this]{run();});
 }
 void push(const AuditRecord& r){if(enabled_&&!failed)queue_.push(r);}
 uint64_t drops()const{return queue_.dropped.load();}
 ~Audit(){stop_=true;if(worker_.joinable())worker_.join();}
};
class Publisher {
 std::atomic<bool> stop_{false};std::thread worker_;Json manifest_;int port_;
 void run() noexcept {
  void* context=zmq_ctx_new();void* socket=context?zmq_socket(context,ZMQ_PUB):nullptr;
  if(!socket){failed=true;if(context)zmq_ctx_term(context);return;}
  int zero=0,hwm=16;zmq_setsockopt(socket,ZMQ_LINGER,&zero,sizeof(zero));zmq_setsockopt(socket,ZMQ_SNDHWM,&hwm,sizeof(hwm));
  auto endpoint="tcp://127.0.0.1:"+std::to_string(port_);
  if(zmq_bind(socket,endpoint.c_str())){failed=true;zmq_close(socket);zmq_ctx_term(context);return;}
  uint64_t publication_sequence=0;
  auto send=[&](Json j){j["publication_sequence"]=++publication_sequence;j["native_session"]=id;j["published_monotonic_ns"]=mono();j["queue_drops"]=inference.dropped.load()+writer.dropped.load();
   auto s=j.dump();if(s.size()>1048576){++serialization_drops;return;}
   const std::string topic="om/sonic/native/v2-draft";
   if(zmq_send(socket,topic.data(),topic.size(),ZMQ_SNDMORE|ZMQ_DONTWAIT)<0||zmq_send(socket,s.data(),s.size(),ZMQ_DONTWAIT)<0)++serialization_drops;
  };
  try {Audit audit(port_,id);uint64_t last=0;Record r;
   auto publish=[&](const Record& r){send(encode(r));audit.push({mono(),r.index,r.stream,r.seq,r.at,r.finished,r.send_ns,inference.dropped.load(),writer.dropped.load(),serialization_drops.load(),inference.last_dropped_index.load(),writer.last_dropped_sequence.load(),publication_sequence,r.kind,r.inference_ok,r.command_ok,r.authority});};
   while(!stop_){auto now=mono();if(now-last>=500000000){send({{"kind","manifest"},{"manifest",manifest_},{"audit_drops",audit.drops()},{"audit_failed",audit.failed.load()},{"serialization_drops",serialization_drops.load()}});last=now;}
    for(int i=0;i<16&&inference.pop(r);++i)publish(r);
    for(int i=0;i<16&&writer.pop(r);++i)publish(r);
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
   }
  }catch(...){failed=true;}
  zmq_close(socket);zmq_ctx_term(context);
 }
public:
 const std::string id=session();Ring<Record> inference,writer;std::atomic<bool> failed{false};std::atomic<uint64_t> serialization_drops{0};
 Publisher(int port,Json manifest):manifest_(std::move(manifest)),port_(port){if(port<1024||port>65535)throw std::runtime_error("invalid telemetry port");worker_=std::thread([this]{run();});}
 ~Publisher(){stop_=true;if(worker_.joinable())worker_.join();}
};
// On every exit (including inference/postprocess rejection), preserve the attempt
// as diagnostics. A downstream join can only consume successful committed ticks.
struct Attempt {Publisher* pub;Record& record;~Attempt(){if(pub){if(!record.finished)record.finished=mono();if(record.cpu_started)record.cpu_elapsed=thread_cpu()-record.cpu_started;if(record.control_cpu_started){record.control_cpu_elapsed=thread_cpu()-record.control_cpu_started;rusage usage{};getrusage(RUSAGE_THREAD,&usage);record.voluntary_switches=usage.ru_nvcsw-record.voluntary_started;record.involuntary_switches=usage.ru_nivcsw-record.involuntary_started;}pub->inference.push(record);}}};
}
