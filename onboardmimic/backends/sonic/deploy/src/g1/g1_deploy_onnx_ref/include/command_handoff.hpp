#pragma once
#include <mutex>
#include "utils.hpp"

// A read for sending and a replacement are ordered by the same mutex. A retired
// value never selected by the sole writer cannot subsequently be sent by it.
// No socket/GPU work is performed under this lock.
template<class T> class CommandHandoff {
  DataBuffer<T> buffer_;
  std::mutex mutex_;
  bool selected_ = false;
 public:
  struct Replacement { std::shared_ptr<const T> previous; bool selected; };
  Replacement SetData(const T& value) {
    std::lock_guard<std::mutex> guard(mutex_);
    Replacement result{buffer_.GetDataWithTime().data, selected_};
    buffer_.SetData(value);selected_=false;return result;
  }
  TimestampedData<T> GetDataWithTime() const {return buffer_.GetDataWithTime();}
  TimestampedData<T> SelectForSend() {
    std::lock_guard<std::mutex> guard(mutex_);
    auto result=buffer_.GetDataWithTime();selected_=bool(result.data);return result;
  }
};

