#pragma once
#include <zmq.h>
#include <msgpack.hpp>
#include <array>
#include <atomic>
#include <chrono>
#include <stdexcept>
#include <string>
#include <thread>
#include <time.h>
#include <unistd.h>
namespace sonic_bridge {
inline uint64_t ns() { timespec t{};clock_gettime(CLOCK_MONOTONIC,&t);return uint64_t(t.tv_sec)*1000000000+t.tv_nsec; }
inline uint64_t cpu_ns() { timespec t{};clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t);return uint64_t(t.tv_sec)*1000000000+t.tv_nsec; }
inline std::string endpoint(int port) { if(port<1024||port>65535)throw std::runtime_error("invalid port");return "tcp://127.0.0.1:"+std::to_string(port); }
struct Context { void* ptr=zmq_ctx_new();~Context(){zmq_ctx_term(ptr);} };
struct Socket {
  void* ptr;
  Socket(Context& c,int type):ptr(zmq_socket(c.ptr,type)) { if(!ptr)throw std::runtime_error("zmq_socket");int zero=0;zmq_setsockopt(ptr,ZMQ_LINGER,&zero,4); }
  ~Socket(){zmq_close(ptr);}
  void set(int k,int v){if(zmq_setsockopt(ptr,k,&v,4))throw std::runtime_error("zmq option");}
  void bind(int port){if(zmq_bind(ptr,endpoint(port).c_str()))throw std::runtime_error("zmq bind");}
  void connect(int port){if(zmq_connect(ptr,endpoint(port).c_str()))throw std::runtime_error("zmq connect");}
  void subscribe(const char* s=""){if(zmq_setsockopt(ptr,ZMQ_SUBSCRIBE,s,strlen(s)))throw std::runtime_error("zmq subscribe");}
};
template<typename T,size_t N=8192> class Ring {
  std::array<T,N> slots{}; alignas(64) std::atomic<size_t> head{0};alignas(64) std::atomic<size_t> tail{0};
public:
  std::atomic<uint64_t> dropped{0};
  bool push(const T& v) {size_t h=head.load(std::memory_order_relaxed);if(h-tail.load(std::memory_order_acquire)==N){++dropped;return false;}slots[h%N]=v;head.store(h+1,std::memory_order_release);return true;}
  bool pop(T& v) {size_t t=tail.load(std::memory_order_relaxed);if(t==head.load(std::memory_order_acquire))return false;v=slots[t%N];tail.store(t+1,std::memory_order_release);return true;}
};
inline const msgpack::object& field(const msgpack::object& o,const char* key) {
  if(o.type!=msgpack::type::MAP)throw std::runtime_error("expected map");
  for(uint32_t i=0;i<o.via.map.size;++i) {auto& kv=o.via.map.ptr[i];if(kv.key.type==msgpack::type::STR&&kv.key.via.str.size==strlen(key)&&!memcmp(kv.key.via.str.ptr,key,strlen(key)))return kv.val;}
  throw std::runtime_error("missing field");
}
}
