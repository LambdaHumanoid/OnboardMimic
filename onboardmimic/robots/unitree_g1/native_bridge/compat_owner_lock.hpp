#pragma once
#include "owner_lock.hpp"
// Shared admission lock for interoperability with previously installed bridges.
// Acquiring it is mandatory for physical control, including when the new lock is held.
namespace sonic_bridge {
inline const char* previous_owner_lock() { return "/tmp/sonic-motor-control.lock"; }
class CompatibleOwnerLock {
  OwnerLock current_, previous_;
public:
  CompatibleOwnerLock(const std::string& current, const std::string& previous)
    : current_(current), previous_(previous) {}
};
}
