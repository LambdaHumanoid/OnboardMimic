// Isolated DDS test. Never creates a motor publisher or a physical voice topic.
#include "protection_voice.hpp"
#include <cassert>
#include <iostream>
int main(){
 const std::string domain=getenv("ROS_DOMAIN_ID")?getenv("ROS_DOMAIN_ID"):"";
 const std::string xml=getenv("CYCLONEDDS_URI")?getenv("CYCLONEDDS_URI"):"";
 if(domain!="94"||xml.find("<NetworkInterfaceAddress>lo</NetworkInterfaceAddress>")==std::string::npos)return 2;
 rclcpp::init(0,nullptr);
 auto node=std::make_shared<rclcpp::Node>("protection_voice_test");
 int count=0;std::vector<uint8_t> received;std::string stream;unsigned chunks=0;
 auto reply=node->create_publisher<unitree_api::msg::Response>("/onboardmimic/diagnostics/protection_voice/response",rclcpp::QoS(1));
 auto sub=node->create_subscription<unitree_api::msg::Request>("/onboardmimic/diagnostics/protection_voice/request",rclcpp::QoS(1),[&](unitree_api::msg::Request::ConstSharedPtr request){
  assert(request->header.identity.api_id==1003);
  assert(!request->binary.empty()&&request->binary.size()<=8000);
  auto parameter=nlohmann::json::parse(request->parameter);assert(parameter["app_name"]=="onboardmimic_protection");
  if(received.empty()){stream=parameter["stream_id"].get<std::string>();chunks=0;}
  assert(parameter["stream_id"]==stream);++chunks;
  received.insert(received.end(),request->binary.begin(),request->binary.end());
  assert(received.size()<=2*sonic_bridge::protection_voice_pcm.size());
  if(received.size()==2*sonic_bridge::protection_voice_pcm.size()){
   for(size_t i=0;i<received.size();++i)assert(received[i]==sonic_bridge::protection_voice_pcm[i%sonic_bridge::protection_voice_pcm.size()]);
   assert(chunks==(2*sonic_bridge::protection_voice_pcm.size()+7999)/8000);++count;received.clear();
  }
  unitree_api::msg::Response response;response.header.identity=request->header.identity;response.header.status.code=0;reply->publish(response);
 });
 {
  sonic_bridge::ProtectionVoice voice(false);
  auto wait=[&](auto predicate){auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5+2*sonic_bridge::protection_voice_pcm.size()/32000);while(!predicate()){assert(std::chrono::steady_clock::now()<deadline);rclcpp::spin_some(node);std::this_thread::sleep_for(std::chrono::milliseconds(5));}};
  wait([&]{return sub->get_publisher_count()>0&&reply->get_subscription_count()>0;});
  voice.signal.notify(1,false);assert(voice.signal.pending.load()==0);
  assert(voice.signal.notify(1,true));wait([&]{return count==1&&voice.response_code.load()==0;});
  for(int i=0;i<100000;++i)assert(!voice.signal.notify(1,true));
  assert(voice.signal.notify(2,true));wait([&]{return count==2&&voice.response_code.load()==0;});
  assert(!voice.failed.load());
 }
 rclcpp::shutdown();
 std::cout<<"Voice DDS passed: 2 generations, one batch each, 2 exact bilingual clips per batch; no hardware\n";
}
