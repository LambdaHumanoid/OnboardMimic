import ctypes,struct,time
from pathlib import Path
import numpy as np
class Encoder:
    def __init__(self):
        self.lib=ctypes.CDLL(str(Path(__file__).with_name('wire_kernel.so')))
        self.lib.crc_command.argtypes=[np.ctypeslib.ndpointer(dtype=np.float64,flags='C_CONTIGUOUS'),ctypes.c_uint];self.lib.crc_command.restype=ctypes.c_uint
        self.sequence=0
    def encode(self,command,machine,stream):
        values=np.ascontiguousarray(command,dtype=np.float64)
        if values.shape!=(29,5) or not np.isfinite(values).all():raise ValueError('invalid command')
        self.sequence+=1;crc=self.lib.crc_command(values,int(machine))
        header=struct.pack('<4sHHQQQBBBB4II',b'LCU1',1,0,stream,self.sequence,time.monotonic_ns(),0,int(machine),35,0,0,0,0,0,crc)
        return header+b''.join(struct.pack('<B5fI',1,*row,0) for row in values)+bytes(6*25)
