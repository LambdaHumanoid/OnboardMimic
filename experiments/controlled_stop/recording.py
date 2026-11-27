"""Bounded non-blocking flight recorder. No controller or motion interfaces."""
import json,queue,threading,time,hashlib
from pathlib import Path
import numpy as np

def encode(value):
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    raise TypeError(type(value).__name__)

def json_safe(value):
    """Keep invalid sensor evidence without killing the recorder on NaN/Inf."""
    if isinstance(value,np.ndarray):return json_safe(value.tolist())
    if isinstance(value,np.generic):return json_safe(value.item())
    if isinstance(value,float) and not np.isfinite(value):
        return {'invalid_float':str(value)}
    if isinstance(value,dict):return {k:json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [json_safe(v) for v in value]
    return value

class Recorder:
    def __init__(self,directory,capacity=2048,segment_bytes=25_000_000,segments=4):
        if capacity<1 or segment_bytes<1 or segments<2:raise ValueError('Invalid recorder bounds')
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=False)
        self.ready=threading.Event();self.queue=queue.Queue(capacity);self.closed=threading.Event();self.dropped=0;self.error='';self.written=0
        self.segment_bytes=segment_bytes;self.segments=segments
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
        if not self.ready.wait(1) or self.error:
            self.closed.set();raise RuntimeError('Recorder unavailable: '+self.error)
    def submit(self,record):
        # Caller transfers ownership of immutable snapshot arrays/dictionaries.
        if self.closed.is_set():return
        try:self.queue.put_nowait(record)
        except queue.Full:self.dropped+=1
    def run(self):
        number=0;out=None;last_flush=time.monotonic()
        try:
            out=(self.directory/'000000.jsonl').open('w');self.ready.set()
            while not self.closed.is_set() or not self.queue.empty():
                try:record=self.queue.get(timeout=.1)
                except queue.Empty:
                    out.flush();continue
                out.write(json.dumps(json_safe(record),default=encode,allow_nan=False,separators=(',',':'))+'\n');self.written+=1
                if time.monotonic()-last_flush>.2:out.flush();last_flush=time.monotonic()
                if out.tell()>=self.segment_bytes:
                    out.close();number+=1;out=(self.directory/('%06d.jsonl'%number)).open('w')
                    old_index=number-self.segments+1
                    old=self.directory/('%06d.jsonl'%old_index)
                    if old_index>0 and old.exists():old.unlink()
            out.flush()
        except Exception as e:self.error=repr(e);self.ready.set()
        finally:
            if out:out.close()
            (self.directory/'manifest.json').write_text(json.dumps(dict(written=self.written,dropped=self.dropped,error=self.error)))
    def close(self):
        self.closed.set();self.thread.join(3)


def sample(captured,frame,command,phase,diagnostics=None,emitted=False,sequence=None):
    motors=frame['motor_state'][:29];q=np.array([x['q'] for x in motors]);dq=np.array([x['dq'] for x in motors])
    c=None if command is None else np.asarray(command).copy()
    pd=None if c is None else c[:,3]*(c[:,0]-q)+c[:,4]*(c[:,1]-dq)
    return dict(kind='sample',monotonic=time.monotonic(),wall=time.time(),captured=captured,
        feedback_age_s=time.monotonic()-captured,tick=frame.get('tick'),phase=phase,
        raw_feedback=frame,command=c,estimated_pd_nm=pd,
        estimated_total_target_nm=None if c is None else c[:,2]+pd,
        diagnostics=diagnostics,emitted=emitted,sequence=sequence)


def provenance(root):
    root=Path(root)
    files=('controller.py','worker.py','standing_plan.py','coordinated_plan.py','direct_plan.py','direct_controller.py','effort-profile.json','support.py','native_model.py','model.json','model_kernel.so','trajectory.npz','wire.py','wire_kernel.so')
    return dict(kind='provenance',schema=1,wall=time.time(),monotonic=time.monotonic(),
        sha256={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files if (root/name).is_file()},
        joint_names=json.loads((root/'model.json').read_text())['joint_names'],
        torque_note='tau_est is device-estimated; PD and total target are estimates at sampled feedback, not actual motor output')
