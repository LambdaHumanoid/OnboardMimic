#pragma once
#include <atomic>
#include <cstdint>
namespace sonic_bridge {
// Single motor-writer producer. Notification never allocates, waits, or calls DDS.
struct ProtectionVoiceSignal {
 static_assert(std::atomic<uint64_t>::is_always_lock_free,"Voice signal must be lock-free");
 std::atomic<uint64_t> pending{0};
 uint64_t last_notified=0;
 bool notify(uint64_t generation,bool running){
  if(!running||!generation||generation<=last_notified)return false;
  last_notified=generation;pending.store(generation,std::memory_order_release);return true;
 }
};
}
