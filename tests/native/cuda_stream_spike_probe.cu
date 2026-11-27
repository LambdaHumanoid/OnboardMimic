// Isolated GPU synchronization reproducer: no TensorRT, DDS or robot commands.
#include <cuda_runtime.h>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <algorithm>
#include <cstring>
#include "onboard_cuda_stream.hpp"
void check(cudaError_t s){if(s!=cudaSuccess)throw std::runtime_error(cudaGetErrorString(s));}
__global__ void busy(unsigned long long cycles){auto t=clock64();while(clock64()-t<cycles){}}
__global__ void copy_value(float* data){data[0]+=1;}
int main(){
 cudaDeviceProp prop{};check(cudaGetDeviceProperties(&prop,0));
 float *input,*output,*device;check(cudaMallocHost(&input,4096));check(cudaMallocHost(&output,4096));check(cudaMalloc(&device,4096));
 for(int mode=0;mode<2;++mode){
  cudaStream_t planner,policy;
  if(mode){check(CreateOnboardCudaStream(&planner,false));check(CreateOnboardCudaStream(&policy,true));}
  else {check(cudaStreamCreate(&planner));check(cudaStreamCreate(&policy));}
  std::vector<double> latency;
  for(int i=0;i<32;++i){
   check(cudaDeviceSynchronize());input[0]=float(i);output[0]=-1;
   busy<<<1,1,0,planner>>>((unsigned long long)prop.clockRate*20);check(cudaGetLastError());
   auto start=std::chrono::steady_clock::now();
   if(mode)check(cudaMemcpyAsync(device,input,4096,cudaMemcpyHostToDevice,policy));
   else check(cudaMemcpy(device,input,4096,cudaMemcpyHostToDevice));
   copy_value<<<1,1,0,policy>>>(device);check(cudaGetLastError());
   check(cudaMemcpyAsync(output,device,4096,cudaMemcpyDeviceToHost,policy));check(cudaStreamSynchronize(policy));
   double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
   if(output[0]!=float(i+1))throw std::runtime_error("incorrect ordering/output");
   if(i>=2)latency.push_back(ms);
  }
  std::sort(latency.begin(),latency.end());
  std::cout<<"{\"mode\":\""<<(mode?"explicit_nonblocking_async":"legacy_sync_copy")<<"\",\"median_ms\":"<<latency[latency.size()/2]<<",\"max_ms\":"<<latency.back()<<",\"output_correct\":true}"<<std::endl;
  check(cudaStreamDestroy(planner));check(cudaStreamDestroy(policy));
 }
 check(cudaFree(device));check(cudaFreeHost(input));check(cudaFreeHost(output));
}
