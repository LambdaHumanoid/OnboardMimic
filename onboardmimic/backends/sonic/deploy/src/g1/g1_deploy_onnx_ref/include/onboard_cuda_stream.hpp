#pragma once
#include <cuda_runtime_api.h>
inline cudaError_t CreateOnboardCudaStream(cudaStream_t* stream, bool policy) {
 int least=0, greatest=0;
 cudaError_t result=cudaDeviceGetStreamPriorityRange(&least,&greatest);
 if(result!=cudaSuccess)return result;
 return cudaStreamCreateWithPriority(stream,cudaStreamDefault,policy?greatest:least);
}
