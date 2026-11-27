import ctypes
from pathlib import Path
import numpy as np
from model import Model
class NativeModel(Model):
    def __init__(self,path):
        super().__init__(path)
        self.library=ctypes.CDLL(str(Path(path).with_name('model_kernel.so').resolve()))
        ptr=np.ctypeslib.ndpointer(dtype=np.float64,ndim=1,flags='C_CONTIGUOUS')
        self.library.compute_full.argtypes=[ptr]*5;self.library.compute_full.restype=ctypes.c_int
    def calculate(self,q,dq,quat,gyro):
        args=[np.ascontiguousarray(v,dtype=np.float64) for v in (q,dq,quat,gyro)]
        if [x.shape for x in args]!=[(29,),(29,),(4,),(3,)] or not all(np.isfinite(x).all() for x in args) or not .9<np.linalg.norm(args[2])<1.1:
            raise ValueError('Invalid measured state')
        out=np.zeros(511)
        if self.library.compute_full(*args,out):raise ValueError('Nonfinite model output')
        return dict(com=out[:3],feet=out[3:9].reshape(2,3),feedforward=out[9:38],bias=out[38:73],jacobian=out[73:493].reshape(12,35),foot_rotations=out[493:].reshape(2,3,3))
