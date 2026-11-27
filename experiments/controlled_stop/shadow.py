"""Read-only real-state check. SUB-only: no DDS/UDP motor writer exists here."""
import argparse,json,time
from pathlib import Path
import numpy as np
import msgpack,zmq
from native_model import NativeModel

def run(output):
    model=NativeModel(Path(__file__).with_name('model.json'))
    ctx=zmq.Context();sub=ctx.socket(zmq.SUB);sub.setsockopt(zmq.SUBSCRIBE,b'rt/');sub.setsockopt(zmq.RCVHWM,8);sub.connect('tcp://127.0.0.1:16660')
    last_tick=None;sample=None;continuity=None;source_at=0.;received=0.;elapsed=[];count=0;last_write=0
    def save(value):
        tmp=output.with_suffix('.tmp');tmp.write_text(json.dumps(value,allow_nan=False));tmp.replace(output)
    try:
        while True:
            if sub.poll(10):
                for _ in range(32):
                    try:topic,payload=sub.recv_multipart(zmq.NOBLOCK)
                    except zmq.Again:break
                    value=msgpack.unpackb(payload,raw=False);now=time.monotonic()
                    if topic==b'rt/continuity':
                        continuity=value;source_at=value.get('at',0)
                    if topic!=b'rt/lowstate':continue
                    tick=value.get('tick')
                    if type(tick)!=int or (last_tick is not None and not 0<(tick-last_tick)%2**32<2**31):continue
                    last_tick=tick;received=now;sample=value
            now=time.monotonic()
            fresh=sample is not None and now-received<.05 and continuity is not None and 0<=now-source_at<.2
            status=dict(read_only=True,motor_commands_sent=0,actuation_enabled=False,phase='shadow',fresh=fresh,samples=count,
                blockers=['控制权切换和初始支撑命令尚未完成验证，动作按钮未开放'],updated_wall=time.time())
            if fresh:
                try:
                    motors=sample['motor_state'][:29];q=np.array([x['q'] for x in motors]);dq=np.array([x['dq'] for x in motors]);imu=sample['imu_state']
                    start=time.perf_counter();result=model.calculate(q,dq,imu['quaternion'],imu['gyroscope']);ms=(time.perf_counter()-start)*1000
                    elapsed.append(ms);elapsed=elapsed[-10000:];count+=1
                    # Base IMU in LowState, NOT the torso secondary IMU.
                    status.update(q=q.tolist(),max_joint_speed=float(max(abs(dq))),faults=[i for i,x in enumerate(motors) if x.get('motorstate',0)],
                        com_relative_pelvis=result['com'].tolist(),feedforward_nm=result['feedforward'].tolist(),
                        compute_ms=dict(mean=float(np.mean(elapsed)),p99=float(np.percentile(elapsed,99)),max=max(elapsed)),
                        feedback_age_ms=(now-received)*1000,imu_source='lowstate.imu_state (pelvis)',source=continuity.get('source'))
                except (KeyError,ValueError,TypeError) as e:status.update(fresh=False,error=str(e))
            if now-last_write>.2:save(status);last_write=now
            time.sleep(.002)
    finally:sub.close(0);ctx.term()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.output)
