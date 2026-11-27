#include "channel.hpp"
#include <algorithm>
#include <fstream>
#include <time.h>
using Clock=std::chrono::steady_clock;
double now(){return std::chrono::duration<double>(Clock::now().time_since_epoch()).count();}
double cpu(){timespec t{};clock_gettime(CLOCK_PROCESS_CPUTIME_ID,&t);return t.tv_sec+t.tv_nsec*1e-9;}
int main(int argc,char**argv){
 std::ifstream net("/proc/net/dev");std::string line;while(std::getline(net,line))if(line.find(':')!=std::string::npos&&line.substr(0,line.find(':')).find("lo")==std::string::npos)return 90;
 setenv("G1_WBC_ZMQ_READ_ONLY","1",1);
 zmq::context_t ctx(1);zmq::socket_t pub(ctx,ZMQ_PUB),sink(ctx,ZMQ_SUB);
 pub.bind("tcp://127.0.0.1:17660");sink.set(zmq::sockopt::subscribe,"");sink.bind("tcp://127.0.0.1:17661");
 ZmqRobotChannel ch;std::array<std::atomic<double>,2000> sent{};std::vector<double> ages;ages.reserve(2000);std::atomic<int> errors{0},imus{0};bool stalled=false;double previous_callback=0,max_callback_gap_ms=0;
 ch.SetLowStateCallback([&](const void*p){auto&s=*static_cast<const LowState_*>(p);double arrival=now();if(previous_callback)max_callback_gap_ms=std::max(max_callback_gap_ms,(arrival-previous_callback)*1000);previous_callback=arrival;auto n=s.tick();if(n>=sent.size()||std::abs(s.motor_state()[0].q()-float(n)*.001f)>.00001f){errors++;return;}ages.push_back((now()-sent[n].load())*1000);if(argc>1&&!stalled&&n>=400){stalled=true;std::this_thread::sleep_for(std::chrono::milliseconds(80));}});
 ch.SetImuCallback([&](const void*){imus++;});
 if(!ch.Init("127.0.0.1",17660,17661,false,0,0))return 91;
 auto c=cpu(),t=now();std::this_thread::sleep_for(std::chrono::seconds(1));double idle=(cpu()-c)/(now()-t);
 c=cpu();t=now();auto start=Clock::now();
 for(unsigned i=1;i<=1500;i++){
  std::this_thread::sleep_until(start+std::chrono::microseconds(i*2000));
  msgpack::sbuffer b;msgpack::packer<msgpack::sbuffer> pk(b);pk.pack_map(2);pk.pack("tick");pk.pack(i);pk.pack("motor_state");pk.pack_array(35);
  for(int j=0;j<35;j++){pk.pack_map(3);pk.pack("q");pk.pack(float(i)*.001f);pk.pack("dq");pk.pack(0.f);pk.pack("tau_est");pk.pack(0.f);}
  sent[i]=now();pub.send(zmq::buffer("rt/lowstate",11),zmq::send_flags::sndmore);pub.send(zmq::buffer(b.data(),b.size()),zmq::send_flags::none);
  if(i%2==0){msgpack::sbuffer ib;msgpack::packer<msgpack::sbuffer> ip(ib);ip.pack_map(1);ip.pack("quaternion");ip.pack(std::vector<float>{1,0,0,0});pub.send(zmq::buffer("rt/secondary_imu",16),zmq::send_flags::sndmore);pub.send(zmq::buffer(ib.data(),ib.size()),zmq::send_flags::none);}
 }
 double streaming=(cpu()-c)/(now()-t);std::this_thread::sleep_for(std::chrono::milliseconds(100));t=now();ch.Stop();double stop=(now()-t)*1000;
 zmq::message_t m;bool output=bool(sink.recv(m,zmq::recv_flags::dontwait));std::sort(ages.begin(),ages.end());if(ages.empty())return 92;
 auto q=[&](double f){return ages[size_t(f*(ages.size()-1))];};
 std::cout<<"RESULT {\"idle_cpu_cores\":"<<idle<<",\"stream_cpu_cores\":"<<streaming<<",\"callbacks\":"<<ages.size()<<",\"imu_callbacks\":"<<imus<<",\"errors\":"<<errors<<",\"command_output\":"<<output<<",\"median_ms\":"<<q(.5)<<",\"p95_ms\":"<<q(.95)<<",\"p99_ms\":"<<q(.99)<<",\"max_ms\":"<<ages.back()<<",\"max_callback_gap_ms\":"<<max_callback_gap_ms<<",\"stop_ms\":"<<stop<<"}"<<std::endl;
 return errors||output||imus==0;
}
