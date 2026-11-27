#include "pose_entry_blend.hpp"
#include <cassert>
#include <limits>
int main() {
 pose_entry::Blend b;std::array<float,29> held{},target{};held.fill(.2f);target.fill(1.2f);
 assert(b.Apply(true,10.,held,target));assert(target[15]==held[15]);assert(b.progress==0.);
 target.fill(1.2f);assert(b.Apply(true,11.,held,target));assert(std::abs(target[15]-.7)<1e-6);
 target.fill(1.2f);assert(b.Apply(true,12.,held,target));assert(b.progress==1.);assert(target[22]==1.2f);
 target.fill(.5f);assert(b.Apply(false,12.1,held,target));assert(target[15]==.5f);
 assert(b.Apply(true,13.,held,target));assert(b.progress==0.);
 target[15]=std::numeric_limits<float>::quiet_NaN();assert(!b.Apply(true,13.1,held,target));
}
