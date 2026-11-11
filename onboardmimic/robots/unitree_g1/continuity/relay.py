"""Loopback-only relay. Original state wire format; no motor outputs."""
import argparse,json,time,uuid,os
from .state_mux import StateMux,Frame

def main():
    import zmq,msgpack
    p=argparse.ArgumentParser();p.add_argument('--primary-port',type=int,default=16665);p.add_argument('--standby-port',type=int,default=16666);p.add_argument('--output-port',type=int,default=16660);a=p.parse_args()
    ports=[a.primary_port,a.standby_port,a.output_port];assert len(set(ports))==3 and all(1024<=v<=65535 for v in ports)
    ctx=zmq.Context();pub=ctx.socket(zmq.PUB);pub.setsockopt(zmq.SNDHWM,8);pub.bind('tcp://127.0.0.1:'+str(a.output_port));sources=[]
    for source,port in zip(('primary','standby'),ports):
        sub=ctx.socket(zmq.SUB);sub.setsockopt(zmq.SUBSCRIBE,b'');sub.setsockopt(zmq.RCVHWM,8);sub.connect('tcp://127.0.0.1:'+str(port));sources.append((source,sub))
    mux=StateMux();last_generation=-1;last_log=0;last_status=0
    session=uuid.uuid4().hex;sequence=0
    try:
        while True:
            for source,sub in sources:
                for _ in range(16):
                    try:raw=sub.recv(zmq.NOBLOCK)
                    except zmq.Again:break
                    try:
                        f=Frame(**msgpack.unpackb(raw,raw=False));assert f.source==source
                        mux.ingest(f,time.monotonic())
                    except (ValueError,TypeError,AssertionError,OverflowError):mux.rejected+=1
            now=time.monotonic()
            frames=mux.select(now)
            changed=mux.generation!=last_generation
            if changed or now-last_status>=.1:
                sequence+=1
                healthy={s:all((s,t) in mux.frames and 0<=now-mux.frames[s,t].captured<=mux.max_age for t in ('rt/lowstate','rt/secondary_imu')) for s in ('primary','standby')}
                status=dict(protocol=1,session=session,pid=os.getpid(),sequence=sequence,event='state_mux',source=mux.active,generation=mux.generation,at=now,rejected=mux.rejected,healthy=healthy,latest_event=mux.events[-1] if mux.events else None)
                if changed or now-last_log>=1:
                    print(json.dumps(status),flush=True);last_log=now
                # Emit transitions before new state. The gateway blocks new
                # motion during the brief handover; original safety stays live.
                pub.send_multipart([b'rt/continuity',msgpack.packb(status,use_bin_type=True)],zmq.NOBLOCK)
                last_generation=mux.generation;last_status=now
            for f in frames:pub.send_multipart([f.topic.encode(),f.payload],zmq.NOBLOCK)
            time.sleep(.001)
    finally:
        for _,sub in sources:sub.close(0)
        pub.close(0);ctx.term()

if __name__=='__main__':main()
