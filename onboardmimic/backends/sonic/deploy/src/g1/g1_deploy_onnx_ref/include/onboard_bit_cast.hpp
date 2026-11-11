#pragma once
#include <cstring>
#include <type_traits>
template<class To,class From> To onboard_bit_cast(const From& value) noexcept {
 static_assert(sizeof(To)==sizeof(From));
 static_assert(std::is_trivially_copyable_v<To> && std::is_trivially_copyable_v<From>);
 To out;std::memcpy(&out,&value,sizeof(out));return out;
}
