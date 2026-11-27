#include "core.hpp"
extern "C" unsigned crc_command(const double* values,unsigned machine){
 sonic_bridge::Command c;c.mode_machine=machine;
 for(int i=0;i<29;i++){auto& m=c.motor[i];m.mode=1;m.q=values[5*i];m.dq=values[5*i+1];m.tau=values[5*i+2];m.kp=values[5*i+3];m.kd=values[5*i+4];}return sonic_bridge::crc(c);
}
