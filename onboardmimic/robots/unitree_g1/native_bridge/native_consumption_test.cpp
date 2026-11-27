#include "../../../backends/sonic/deploy/src/g1/g1_deploy_onnx_ref/include/native_consumption.hpp"
#include "../../../backends/sonic/deploy/src/g1/g1_deploy_onnx_ref/include/state_logger.hpp"
#include <cassert>
#include <iostream>
#include <unistd.h>
int main(){
 using namespace sonic_consumption;
 // Real logger with deliberately irregular scheduling; no robot, CSV or GPU.
 StateLogger logger("",64,29,29,.02,false);
 std::array<double,4> quat{1,0,0,0};std::array<double,3> xyz{};
 std::array<double,29> q{},dq{},action{};std::span<double> empty;
 auto append=[&](double value){q.fill(value);auto qs=std::span<double>(q),ds=std::span<double>(dq),as=std::span<double>(action);
  logger.LogFullState(quat,xyz,xyz,quat,xyz,xyz,qs,ds,as,empty,empty,empty,empty,empty,empty,empty,empty,empty);
 };
 append(.1);std::this_thread::sleep_for(std::chrono::milliseconds(1));append(.2);
 auto padded=logger.GetLatest(3,.02,false);
 assert(padded.size()==3&&padded[0].timestamp_monotonic.time_since_epoch().count()==0);
 std::this_thread::sleep_for(std::chrono::milliseconds(3));append(.3);
 auto actual=logger.GetLatest(3,.02,false);
 assert(actual[0].body_q[0]==.1&&actual[2].body_q[0]==.3);
 assert(actual[1].timestamp_monotonic>actual[0].timestamp_monotonic);
 assert(actual[1].timestamp_monotonic-actual[0].timestamp_monotonic!=std::chrono::milliseconds(20));
 auto stride=logger.GetLatest(2,.04,false);assert(stride[0].index==actual[0].index&&stride[1].index==actual[2].index);
 // Snapshot uses float values, preserving exact inference representation.
 Record r;r.index=7;r.input_size=3;
 for(size_t i=0;i<3;++i)r.input[i]=static_cast<float>(actual[i].body_q[0]);
 r.cpu_started=thread_cpu();r.cpu_elapsed=thread_cpu()-r.cpu_started;r.infer_timing={123,456};
 r.encoder_stages={10,20,30,40};r.encoder_timing={60,70,10,20,30};
 r.encoder_gather_cpu_ns=8;r.encoder_infer_cpu_ns=9;
 auto encoded=encode(r);assert(encoded["timing"]["enqueue_wall_ns"]==123);assert(encoded["timing"]["synchronize_wall_ns"]==456);assert(encoded["input"][0].get<float>()==static_cast<float>(.1));
 assert(encoded["timing"]["encoder_stages_ns"]==Json::array({10,20,30,40}));
 assert(encoded["timing"]["encoder"]["input_copy_wall_ns"]==10);
 assert(encoded["timing"]["encoder"]["launch_wall_ns"]==20);
 assert(encoded["timing"]["encoder"]["output_copy_wall_ns"]==30);
 assert(encoded["timing"]["encoder"]["synchronize_wall_ns"]==70);
 assert(encoded["timing"]["encoder_gather_cpu_ns"]==8);
 assert(encoded["timing"]["encoder_infer_cpu_ns"]==9);
 // Bypassed encoder / new tick cannot carry previous tick timing.
 assert(encode(Record{})["timing"]["encoder_stages_ns"]==Json::array({0,0,0,0}));
 r.input[0]=9;assert(encoded["input"][0].get<float>()!=9);
 // Fixed capacity: a stopped consumer causes drops, never blocking or renumbering.
 auto ring=std::make_unique<Ring<Record>>();for(int i=0;i<16;++i){r.index=i;assert(ring->push(r));}
 auto begin=mono();for(int i=0;i<100000;++i)assert(!ring->push(r));
 assert(mono()-begin<1000000000);assert(ring->dropped==100000);
 for(int i=0;i<16;++i){Record got;assert(ring->pop(got));assert(got.index==uint64_t(i));}
 assert(!ring->pop(r));
 assert(ring->last_dropped_index==15);
 // Actual background publisher/socket, with synthetic inference values.
 void* context=zmq_ctx_new();void* subscriber=zmq_socket(context,ZMQ_SUB);
 const std::string topic="om/sonic/native/v2-draft";int zero=0;
 zmq_setsockopt(subscriber,ZMQ_SUBSCRIBE,topic.data(),topic.size());zmq_setsockopt(subscriber,ZMQ_LINGER,&zero,sizeof(zero));
 const int port=10000+(getpid()%40000);
 zmq_connect(subscriber,("tcp://127.0.0.1:"+std::to_string(port)).c_str());
 {Publisher publisher(port,{{"test_fixture",true}});bool received=false;
  for(int i=0;i<200&&!received;++i){
   r.kind=0;r.input_size=1;r.input[0]=.125f;r.index=i+1;publisher.inference.push(r);
   zmq_pollitem_t item{subscriber,0,ZMQ_POLLIN,0};zmq_poll(&item,1,5);
   if(item.revents&ZMQ_POLLIN){
    std::array<char,64> first{};auto n=zmq_recv(subscriber,first.data(),first.size(),0);
    assert(std::string(first.data(),n)==topic);int more=0;size_t size=sizeof(more);
    zmq_getsockopt(subscriber,ZMQ_RCVMORE,&more,&size);assert(more);
    std::array<char,200000> payload{};n=zmq_recv(subscriber,payload.data(),payload.size(),0);assert(n>0);
    auto j=Json::parse(payload.data(),payload.data()+n);
    zmq_getsockopt(subscriber,ZMQ_RCVMORE,&more,&size);assert(!more);
    if(j["kind"]==0){assert(j["input"][0]==.125);assert(j["native_session"]==publisher.id);received=true;}
   }
  }
  assert(received&&!publisher.failed);
 }
 zmq_close(subscriber);zmq_ctx_term(context);
 assert(digest("abc")=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
 std::cout<<"native actual logger history / padding / stride / float snapshot / bounded drop tests passed; no hardware\n";
}
