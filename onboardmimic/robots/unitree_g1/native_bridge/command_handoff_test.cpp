#include "../../../backends/sonic/deploy/src/g1/g1_deploy_onnx_ref/include/command_handoff.hpp"
#include <cassert>
#include <thread>
#include <atomic>
struct Command {unsigned index;};
int main(){
 CommandHandoff<Command> b;
 assert(!b.SetData({1}).previous);
 assert(b.GetDataWithTime().data->index==1); // diagnostics do not claim selection
 auto lost=b.SetData({2});assert(lost.previous->index==1&&!lost.selected);
 auto in_flight=b.SelectForSend();assert(in_flight.data->index==2);
 auto replaced=b.SetData({3});assert(replaced.previous->index==2&&replaced.selected);
 assert(in_flight.data->index==2); // an in-flight send must NEVER be proven absent
 for(unsigned i=4;i<300;++i){
  std::atomic<bool> start{false};TimestampedData<Command> selected;
  std::thread writer([&]{while(!start.load()){}selected=b.SelectForSend();});
  start=true;auto old=b.SetData({i});writer.join();
  if(!old.selected)assert(selected.data->index!=old.previous->index);
 }
}
