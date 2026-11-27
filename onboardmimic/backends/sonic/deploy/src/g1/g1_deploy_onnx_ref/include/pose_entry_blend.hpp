#pragma once
#include "control_start_safety.hpp"
#include <algorithm>
namespace pose_entry {
// Control-thread owned. Uses monotonic seconds, not assumed control tick counts.
class Blend {
 public:
  double progress = 1.0;
  bool Apply(bool pose, double now, const std::array<float,29>& held,
             std::array<float,29>& target) {
    if (!pose) { active_=false; progress=1.; return true; }
    if (!control_start_safety::IsFinite(now)) return false;
    if (!active_) { start_=held; began_=now; active_=true; }
    progress=std::clamp((now-began_)/2.0,0.0,1.0);
    const double weight=progress*progress*(3.-2.*progress);
    for (size_t i=0;i<29;++i) {
      if (!control_start_safety::IsFinite(start_[i]) || !control_start_safety::IsFinite(target[i])) return false;
    }
    for (size_t i=0;i<29;++i) target[i]=static_cast<float>((1.-weight)*start_[i]+weight*target[i]);
    return true;
  }
 private:
  bool active_=false;
  double began_=0.;
  std::array<float,29> start_{};
};
}
