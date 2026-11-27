// Pure TensorRT benchmark. No Unitree, DDS, sockets or motor output.
#include <NvInfer.h>
#include <NvInferVersion.h>
#include <cstdint>
#include <cuda_runtime_api.h>
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>
#include <thread>
#include <vector>
#include <chrono>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <stdexcept>
using J=nlohmann::json;
using Clock=std::chrono::steady_clock;
struct Logger:nvinfer1::ILogger {void log(Severity s,const char* m) noexcept override {if(s<=Severity::kERROR)std::cerr<<m<<'\n';}} logger;
void ck(cudaError_t e){if(e!=cudaSuccess)throw std::runtime_error(cudaGetErrorString(e));}
struct Model {
 nvinfer1::IRuntime* runtime; nvinfer1::ICudaEngine* engine; nvinfer1::IExecutionContext* context;
 cudaStream_t stream;cudaGraph_t graph;cudaGraphExec_t executable=nullptr;
 std::vector<void*> device,host;std::vector<size_t> sizes;
 Model(const std::string& path,int priority=0){
  std::ifstream f(path,std::ios::binary);if(!f)throw std::runtime_error(path);
  std::vector<char> data((std::istreambuf_iterator<char>(f)),{});
  runtime=nvinfer1::createInferRuntime(logger);engine=runtime->deserializeCudaEngine(data.data(),data.size());
  if(!engine)throw std::runtime_error("deserialize");context=engine->createExecutionContext();
  ck(cudaStreamCreateWithPriority(&stream,cudaStreamDefault,priority));
  for(int i=0;i<engine->getNbIOTensors();++i){
   auto dtype=engine->getTensorDataType(engine->getIOTensorName(i));
   size_t element=4;
#if NV_TENSORRT_MAJOR >= 10
   if(dtype==nvinfer1::DataType::kINT64)element=8;
   else
#endif
   if(dtype!=nvinfer1::DataType::kFLOAT && dtype!=nvinfer1::DataType::kINT32)throw std::runtime_error("tensor dtype");
   auto d=engine->getTensorShape(engine->getIOTensorName(i));size_t n=1;for(int j=0;j<d.nbDims;++j){if(d.d[j]<1)throw std::runtime_error("shape");n*=d.d[j];}
   sizes.push_back(n*element);void *h,*v;ck(cudaMallocHost(&h,n*element));ck(cudaMalloc(&v,n*element));memset(h,0,n*element);host.push_back(h);device.push_back(v);
   if(!context->setTensorAddress(engine->getIOTensorName(i),v))throw std::runtime_error("tensor address");
  }
  if(path.find("encoder")!=std::string::npos)static_cast<float*>(host[0])[0]=1;
  if(path.find("planner")!=std::string::npos){
   J q;std::ifstream("baseline_q.json")>>q;
   auto p=static_cast<float*>(ptr("context_mujoco_qpos"));
   for(int t=0;t<4;++t){p[t*36+2]=.78874;p[t*36+3]=1;for(int j=0;j<29;++j)p[t*36+7+j]=q[j].get<float>();}
   *static_cast<float*>(ptr("facing_direction"))=1;*static_cast<float*>(ptr("movement_direction"))=1;
   *static_cast<float*>(ptr("height"))=-1;
#if NV_TENSORRT_MAJOR >= 10
   using Integer=int64_t;
#else
   using Integer=int32_t;
#endif
   *static_cast<Integer*>(ptr("random_seed"))=42;
   auto mask=static_cast<Integer*>(ptr("allowed_pred_num_tokens"));mask[3]=mask[4]=mask[5]=1;
  }
  run();ck(cudaStreamBeginCapture(stream,cudaStreamCaptureModeThreadLocal));
  if(!context->enqueueV3(stream))throw std::runtime_error("enqueue capture");
  ck(cudaStreamEndCapture(stream,&graph));ck(cudaGraphInstantiateWithFlags(&executable,graph,0));
 }
 bool input(int i)const{return engine->getTensorIOMode(engine->getIOTensorName(i))==nvinfer1::TensorIOMode::kINPUT;}
 void* ptr(const char* name){for(int i=0;i<engine->getNbIOTensors();++i)if(std::string(engine->getIOTensorName(i))==name)return host[i];throw std::runtime_error(name);}
 void run(){
  for(int i=0;i<engine->getNbIOTensors();++i)if(input(i))ck(cudaMemcpyAsync(device[i],host[i],sizes[i],cudaMemcpyHostToDevice,stream));
  if(executable)ck(cudaGraphLaunch(executable,stream));else if(!context->enqueueV3(stream))throw std::runtime_error("enqueue");
  for(int i=0;i<engine->getNbIOTensors();++i)if(!input(i))ck(cudaMemcpyAsync(host[i],device[i],sizes[i],cudaMemcpyDeviceToHost,stream));
  ck(cudaStreamSynchronize(stream));
  for(int i=0;i<engine->getNbIOTensors();++i)if(!input(i)&&engine->getTensorDataType(engine->getIOTensorName(i))==nvinfer1::DataType::kFLOAT)
   for(size_t k=0;k<sizes[i]/4;++k)if(!std::isfinite(static_cast<float*>(host[i])[k]))throw std::runtime_error("nonfinite");
 }
 ~Model(){cudaGraphExecDestroy(executable);cudaGraphDestroy(graph);delete context;delete engine;delete runtime;for(auto p:host)cudaFreeHost(p);for(auto p:device)cudaFree(p);cudaStreamDestroy(stream);}
};
J stats(std::vector<double> x,double budget){std::sort(x.begin(),x.end());double sum=0;int over=0;for(auto t:x){sum+=t;if(t>budget)++over;}return {{"n",x.size()},{"mean_ms",sum/x.size()},{"p99_ms",x[std::min(x.size()-1,size_t(x.size()*.99))]},{"max_ms",x.back()},{"over_budget",over},{"budget_ms",budget}};}
int main(int argc,char**argv){try{
 int seconds=argc>1?std::stoi(argv[1]):60;bool priority=argc>2&&std::string(argv[2])=="priority";
 if(seconds<1||seconds>3600)return 2;ck(cudaSetDevice(0));int low,high;ck(cudaDeviceGetStreamPriorityRange(&low,&high));
 Model enc("engines/encoder_fp32.engine",priority?high:0),dec("engines/decoder_fp32.engine",priority?high:0),plan("engines/planner_fp32.engine",priority?low:0);
 int token=-1;for(int i=0;i<enc.engine->getNbIOTensors();++i)if(!enc.input(i))token=i;
 if(token<0||enc.sizes[token]!=64*4)throw std::runtime_error("token shape");
 auto policy=[&]{enc.run();memcpy(dec.ptr("obs_dict"),enc.host[token],64*4);dec.run();};
 for(int i=0;i<20;++i){policy();plan.run();}
 J pr,pl;std::exception_ptr pe,le;auto start=Clock::now()+std::chrono::milliseconds(200);
 auto worker=[&](int ms,auto fn,J& out,std::exception_ptr& error){try{ck(cudaSetDevice(0));std::vector<double>w,c;J late=J::array();
  for(int i=0;i<seconds*1000/ms;++i){auto tick=start+std::chrono::milliseconds(i*ms);std::this_thread::sleep_until(tick);auto a=Clock::now();fn();auto b=Clock::now();double work=std::chrono::duration<double,std::milli>(b-a).count(),complete=std::chrono::duration<double,std::milli>(b-tick).count();w.push_back(work);c.push_back(complete);if(complete>ms)late.push_back({{"index",i},{"work_ms",work},{"completion_ms",complete}});}
  out={{"work",stats(w,ms)},{"completion",stats(c,ms)},{"late_cycles",late}};
 }catch(...){error=std::current_exception();}};
 std::thread p([&]{worker(20,policy,pr,pe);}),l([&]{worker(100,[&]{plan.run();},pl,le);});p.join();l.join();if(pe)std::rethrow_exception(pe);if(le)std::rethrow_exception(le);
 std::cout<<J({{"duration_s",seconds},{"motor_output",false},{"synthetic_observations",true},{"priority_streams",priority},{"cuda_stream_flags","default"},{"policy",pr},{"planner",pl}}).dump(2)<<'\n';
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
