#pragma once
#include "protection_voice_signal.hpp"
#include "protection_voice_pcm.hpp"
#include <rclcpp/rclcpp.hpp>
#include <unitree_api/msg/request.hpp>
#include <unitree_api/msg/response.hpp>
#include <nlohmann/json.hpp>
#include <thread>
#include <vector>
#include <chrono>
// Foxy infers a ROS timestamp from any member named "header". Unitree RPC
// headers contain identity/status, not a ROS Header (same compatibility issue
// addressed by the installed Unitree ROS2 example's common/patch.hpp).
namespace libstatistics_collector::topic_statistics_collector {
template<> struct TimeStamp<unitree_api::msg::Request> {
 static std::pair<bool,int64_t> value(const unitree_api::msg::Request&){return {false,0};}
};
template<> struct TimeStamp<unitree_api::msg::Response> {
 static std::pair<bool,int64_t> value(const unitree_api::msg::Response&){return {false,0};}
};
}
namespace sonic_bridge {
// Independent of SONIC and the motor lock. Playback failure is advisory only.
// Audio API 1003 / PCM format follows the locally installed Unitree ROS2 client.
class ProtectionVoice {
 std::atomic<bool> stopping{false};
 std::thread worker;
 void run(bool physical) noexcept {
  try {
   auto node=std::make_shared<rclcpp::Node>("onboardmimic_protection_voice");
   const std::string topic=physical?"/api/voice":"/onboardmimic/diagnostics/protection_voice";
   auto pub=node->create_publisher<unitree_api::msg::Request>(topic+"/request",rclcpp::QoS(1));
   uint64_t expected_id=0;
   auto sub=node->create_subscription<unitree_api::msg::Response>(topic+"/response",rclcpp::QoS(1),[&](unitree_api::msg::Response::ConstSharedPtr response){
    if(expected_id&&uint64_t(response->header.identity.id)==expected_id)response_code.store(response->header.status.code);
   });
   std::vector<uint8_t> batch;batch.reserve(protection_voice_pcm.size()*2);
   for(int repeat=0;repeat<2;++repeat)batch.insert(batch.end(),protection_voice_pcm.begin(),protection_voice_pcm.end());
   uint64_t seen=0;size_t offset=batch.size();std::string stream;
   auto due=std::chrono::steady_clock::now();
   while(!stopping.load()&&rclcpp::ok()){
    auto generation=signal.pending.load(std::memory_order_acquire);
    if(generation>seen&&offset==batch.size()){
     seen=generation;response_code.store(-1);offset=0;published_chunks.store(0);
     due=std::chrono::steady_clock::now();stream=std::to_string(due.time_since_epoch().count());
    }
    if(offset<batch.size()&&std::chrono::steady_clock::now()>=due){
     unitree_api::msg::Request request;
     expected_id=uint64_t(std::chrono::steady_clock::now().time_since_epoch().count());
     request.header.identity.id=expected_id;request.header.identity.api_id=1003;
     request.parameter=nlohmann::json({{"app_name","onboardmimic_protection"},{"stream_id",stream}}).dump();
     // The physical voice endpoint did not play a whole 164 kB batch. Verified
     // on this robot with <=8000-byte, 250 ms chunks sharing one stream ID.
     const auto count=std::min(size_t(8000),batch.size()-offset);
     request.binary.assign(batch.begin()+offset,batch.begin()+offset+count);
     pub->publish(request);offset+=count;published_chunks.fetch_add(1);
     due=std::chrono::steady_clock::now()+std::chrono::microseconds(count*1000000/32000);
     if(offset==batch.size())published_generation.store(seen);
    }
    rclcpp::spin_some(node);
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
   }
  }catch(...){failed.store(true);}
 }
 public:
 ProtectionVoiceSignal signal;
 std::atomic<uint64_t> published_generation{0};
 std::atomic<unsigned> published_chunks{0};
 std::atomic<int> response_code{-1};
 std::atomic<bool> failed{false};
 explicit ProtectionVoice(bool physical){worker=std::thread([this,physical]{run(physical);});}
 ~ProtectionVoice(){stopping.store(true);if(worker.joinable())worker.join();}
};
}
