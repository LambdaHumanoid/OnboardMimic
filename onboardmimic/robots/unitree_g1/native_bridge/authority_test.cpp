#include "authority.hpp"
#include "owner_lock.hpp"
#include "compat_owner_lock.hpp"
#include <cassert>
#include <sys/wait.h>
#include <signal.h>
int main(){
 using A=sonic_bridge::WriterAuthority<24>;A::Gid own{},other{},unknown{};own[0]=1;other[0]=2;
 A a(own,10);assert(!a.acquire(12.9));a.observe(own);assert(a.acquire(13));
 a.observe(other);assert(a.owned&&a.ready(14)&&a.foreign_count==1&&!a.conflict);
 a.observe(unknown);assert(a.owned&&a.ready(50)&&a.foreign_count==2);
 A b(own,0);b.observe(other);assert(!b.acquire(3));
 A c(unknown,0);assert(!c.acquire(3));
 char path[]="/tmp/sonic-owner-test-XXXXXX";int file=mkstemp(path);assert(file>=0);close(file);
 {
  sonic_bridge::OwnerLock owner(path);
  auto child=fork();assert(child>=0);
  if(child==0){try{sonic_bridge::OwnerLock contender(path);_exit(1);}catch(...){_exit(0);}}
  int status=0;assert(waitpid(child,&status,0)==child);assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);
 }
 {sonic_bridge::OwnerLock successor(path);}
 int signal_pipe[2];assert(pipe(signal_pipe)==0);auto child=fork();assert(child>=0);
 if(child==0){close(signal_pipe[0]);sonic_bridge::OwnerLock owner(path);char c='x';assert(write(signal_pipe[1],&c,1)==1);pause();_exit(1);}
 close(signal_pipe[1]);char byte=0;assert(read(signal_pipe[0],&byte,1)==1);close(signal_pipe[0]);
 assert(kill(child,SIGKILL)==0);int status=0;assert(waitpid(child,&status,0)==child);
 {sonic_bridge::OwnerLock after_crash(path);}
 char current[]="/tmp/onboardmimic-owner-test-XXXXXX";file=mkstemp(current);assert(file>=0);close(file);
 {
  sonic_bridge::OwnerLock previous(path);
  auto contender=fork();assert(contender>=0);
  if(contender==0){try{sonic_bridge::CompatibleOwnerLock next(current,path);_exit(1);}catch(...){_exit(0);}}
  assert(waitpid(contender,&status,0)==contender);assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);
  // A failed second acquisition must release the new lock as well.
  sonic_bridge::OwnerLock available(current);
 }
 {
  sonic_bridge::CompatibleOwnerLock next(current,path);
  auto contender=fork();assert(contender>=0);
  if(contender==0){try{sonic_bridge::OwnerLock previous(path);_exit(1);}catch(...){_exit(0);}}
  assert(waitpid(contender,&status,0)==contender);assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);
 }
 {sonic_bridge::CompatibleOwnerLock successor(current,path);}
 unlink(current);
 unlink(path);
}
