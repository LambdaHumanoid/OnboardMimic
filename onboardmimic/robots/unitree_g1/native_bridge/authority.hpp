#pragma once
#include <array>
#include <algorithm>
#include <cstdint>

namespace sonic_bridge {
// Admission is fail-closed; a later sender never revokes the incumbent.
// The process-wide ownership lock is separate from DDS observations.
template<size_t N> struct WriterAuthority {
  using Gid=std::array<uint8_t,N>;
  Gid own,last_foreign{};
  double started;
  bool conflict=false,owned=false;
  uint64_t foreign_count=0;
  WriterAuthority(Gid id,double now):own(id),started(now) {
    if(std::all_of(own.begin(),own.end(),[](uint8_t b){return b==0;}))conflict=true;
  }
  void observe(const Gid& sender) {
    if(sender!=own){last_foreign=sender;++foreign_count;if(!owned)conflict=true;}
  }
  bool ready(double now) const { return owned||(!conflict&&now-started>=3.0); }
  bool acquire(double now) { if(!ready(now))return false;owned=true;return true; }
};
}
