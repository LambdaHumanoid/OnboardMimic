// Software-only producer latency probe: no decoder, SDK, DDS or motor transport.
#include "native_consumption.hpp"
#include <iostream>
#include <cassert>
#include <unistd.h>
int main(){
 using namespace sonic_consumption;
 Record diagnostic;diagnostic.control_stages={1,2,3,4,5,6};diagnostic.control_cpu_elapsed=7;
 diagnostic.command_buffered_ns=8;diagnostic.voluntary_switches=9;diagnostic.involuntary_switches=10;
 auto timing=encode(diagnostic).at("timing");
 assert(timing.at("control_stages_ns").at(3)==4);
 assert(timing.at("control_thread_cpu_ns")==7 && timing.at("command_buffered_ns")==8);
 assert(timing.at("voluntary_switches")==9 && timing.at("involuntary_switches")==10);
 for(int enabled: {0,1}){
  setenv("OM_SONIC_AUDIT",enabled?"1":"0",1);
  Publisher pub(20000+getpid()%20000,{{"test_fixture",true}});
  Record inference;inference.kind=0;inference.input_size=994;inference.encoder_size=1762;
  inference.inference_ok=inference.command_ok=true;
  for(auto& h:inference.histories){h.count=10;}
  Record mapping;mapping.kind=1;mapping.stream=1;
  std::vector<uint64_t> latency;auto origin=std::chrono::steady_clock::now();uint64_t missed=0;
  for(int i=0;i<1500;++i){
   auto due=origin+std::chrono::microseconds(2000*i);std::this_thread::sleep_until(due);
   if(std::chrono::steady_clock::now()-due>std::chrono::milliseconds(2))++missed;
   auto before=mono();mapping.seq=i+1;mapping.index=i/10+1;pub.writer.push(mapping);
   if(i%10==0){inference.index=i/10+1;pub.inference.push(inference);}
   latency.push_back(mono()-before);
  }
  std::sort(latency.begin(),latency.end());
  std::cout<<Json({{"audit",enabled},{"producer_p99_us",latency[latency.size()*99/100]/1000.},
   {"producer_max_us",latency.back()/1000.},{"late_2ms_ticks",missed},
   {"inference_drops",pub.inference.dropped.load()},{"mapping_drops",pub.writer.dropped.load()},
   {"publisher_failed",pub.failed.load()},{"physical",false}}).dump()<<std::endl;
 }
}
