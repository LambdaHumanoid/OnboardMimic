#pragma once
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdexcept>
#include <string>

namespace sonic_bridge {
// Shared by all deployment variants, independent of their ports/directories.
// Never unlink this file: lock lifetime is the open file description, not PID text.
class OwnerLock {
  int fd_=-1;
public:
  explicit OwnerLock(const std::string& path) {
    fd_=open(path.c_str(),O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd_<0)throw std::runtime_error("CONTROL_OWNER_LOCK_UNAVAILABLE: "+path);
    struct stat s{};
    if(fstat(fd_,&s)||!S_ISREG(s.st_mode)||s.st_nlink!=1){close(fd_);fd_=-1;throw std::runtime_error("CONTROL_OWNER_LOCK_INVALID");}
    if(flock(fd_,LOCK_EX|LOCK_NB)){close(fd_);fd_=-1;throw std::runtime_error("CONTROL_OWNER_BUSY: another bridge owns motor control; incumbent unchanged");}
  }
  ~OwnerLock(){if(fd_>=0)close(fd_);}
  OwnerLock(const OwnerLock&)=delete;
  OwnerLock& operator=(const OwnerLock&)=delete;
};
}
