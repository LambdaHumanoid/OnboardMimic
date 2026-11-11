// Read-only process: this executable never creates a motor command publisher.
#include "support.hpp"
#include <rclcpp/rclcpp.hpp>
#include <unitree_hg/msg/low_state.hpp>
#include <unitree_hg/msg/imu_state.hpp>
#include <iostream>
#include <fstream>
using P=msgpack::packer<msgpack::sbuffer>;
template<class T> void kv(P& p,const char* k,const T& v){p.pack(std::string(k));p.pack(v);}
template<class T> void arr(P& p,const char* k,const T& v){p.pack(std::string(k));p.pack_array(v.size());for(auto x:v)p.pack(x);}
void imu(P& p,const unitree_hg::msg::IMUState& s){
 p.pack_map(5);arr(p,"quaternion",s.quaternion);arr(p,"gyroscope",s.gyroscope);
 arr(p,"accelerometer",s.accelerometer);arr(p,"rpy",s.rpy);kv(p,"temperature",s.temperature);
}
void low(P& p,const unitree_hg::msg::LowState& s){
 p.pack_map(9);arr(p,"version",s.version);kv(p,"mode_pr",s.mode_pr);kv(p,"mode_machine",s.mode_machine);kv(p,"tick",s.tick);
 p.pack(std::string("imu_state"));imu(p,s.imu_state);p.pack(std::string("motor_state"));p.pack_array(s.motor_state.size());
 for(const auto& m:s.motor_state){p.pack_map(10);kv(p,"mode",m.mode);kv(p,"q",m.q);kv(p,"dq",m.dq);kv(p,"ddq",m.ddq);kv(p,"tau_est",m.tau_est);
 arr(p,"temperature",m.temperature);kv(p,"vol",m.vol);arr(p,"sensor",m.sensor);kv(p,"motorstate",m.motorstate);arr(p,"reserve",m.reserve);}
 arr(p,"wireless_remote",s.wireless_remote);arr(p,"reserve",s.reserve);kv(p,"crc",s.crc);
}
int main(int argc,char** argv){try{
 std::string source;int port=0;
 for(int i=1;i<argc;i++){std::string a=argv[i];if(a=="--source"&&i+1<argc)source=argv[++i];else if(a=="--port"&&i+1<argc)port=std::stoi(argv[++i]);else throw std::runtime_error("arguments");}
 if(source!="primary"&&source!="standby")throw std::runtime_error("source");
 std::string session;std::ifstream("/proc/sys/kernel/random/uuid")>>session;
 sonic_bridge::Context ctx;sonic_bridge::Socket pub(ctx,ZMQ_PUB);pub.set(ZMQ_SNDHWM,8);pub.bind(port);
 rclcpp::init(0,nullptr);auto node=std::make_shared<rclcpp::Node>("sonic_native_state_"+source);
 auto qos=rclcpp::QoS(rclcpp::KeepLast(1)).best_effort();
 msgpack::sbuffer payload(16384),envelope(16384);std::array<std::atomic<uint64_t>,2> count{},input{};std::array<uint64_t,2> due{};std::atomic<uint64_t> failures{0};
 auto send=[&](int index,double captured,uint32_t tick){
  envelope.clear();P p(envelope);p.pack_map(7);kv(p,"source",source);kv(p,"topic",index==0?std::string("rt/lowstate"):std::string("rt/secondary_imu"));
  kv(p,"session",session);kv(p,"sequence",++count[index]);kv(p,"captured",captured);
  p.pack(std::string("payload"));p.pack_bin(payload.size());p.pack_bin_body(payload.data(),payload.size());p.pack(std::string("tick"));if(index==0)p.pack(tick);else p.pack_nil();
  if(zmq_send(pub.ptr,envelope.data(),envelope.size(),ZMQ_DONTWAIT)<0)++failures;
 };
 // Bound traffic before serialization: forwarding the hardware's 1kHz per
 // topic verbatim can fill the existing ZMQ/Python relay's TCP queues. Only a
 // newly received frame can be sent; missed slots are skipped, never replayed.
 auto eligible=[&](size_t index,uint64_t at){
  ++input[index];uint64_t period=index==0?2000000:4000000;
  if(at<due[index])return false;
  due[index]=due[index]?due[index]+((at-due[index])/period+1)*period:at+period;
  return true;
 };
 auto ls=node->create_subscription<unitree_hg::msg::LowState>("/lowstate",qos,[&](unitree_hg::msg::LowState::SharedPtr s){auto at=sonic_bridge::ns();if(!eligible(0,at))return;payload.clear();P p(payload);low(p,*s);send(0,at/1e9,s->tick);});
 auto is=node->create_subscription<unitree_hg::msg::IMUState>("/secondary_imu",qos,[&](unitree_hg::msg::IMUState::SharedPtr s){auto at=sonic_bridge::ns();if(!eligible(1,at))return;payload.clear();P p(payload);imu(p,*s);send(1,at/1e9,0);});
 std::thread logger([&]{while(rclcpp::ok()){std::cout<<"{\"event\":\"native_state_reader\",\"source\":\""<<source<<"\",\"lowstate\":"<<count[0]<<",\"imu\":"<<count[1]<<",\"lowstate_received\":"<<input[0]<<",\"imu_received\":"<<input[1]<<",\"send_errors\":"<<failures<<"}"<<std::endl;std::this_thread::sleep_for(std::chrono::seconds(1));}});
 rclcpp::spin(node);rclcpp::shutdown();logger.join();return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 1;}}
