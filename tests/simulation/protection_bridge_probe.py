"""Real native writer, fake WBC/state; domain 94, loopback, non-motor topic only."""
import argparse,json,os,signal,socket,struct,subprocess,sys,tempfile,threading,time
from pathlib import Path
import msgpack,zmq
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from onboardmimic.core.protection.client import ProtectionClient

def port(kind):
    with socket.socket(socket.AF_INET,kind) as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def request(path,action,**kw):
    with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as s:
        s.settimeout(.5);s.connect(str(path));s.sendall(json.dumps(dict(protocol=1,action=action,**kw)).encode());r=json.loads(s.recv(8192))
    if not r.get('ok'):raise RuntimeError(r)
    return r

def crc(data):
    value=0xffffffff
    for word, in struct.iter_unpack('<I',data):
        for bit in range(31,-1,-1):
            high=value&0x80000000;value=(value<<1)&0xffffffff
            if high:value^=0x04c11db7
            if word&(1<<bit):value^=0x04c11db7
    return value

def positions():return [-.31,0,0,.67,-.36,0]*2+[0,0,0]+[.2,.2,0,.6,0,0,0]+[.2,-.2,0,.6,0,0,0]+[0]*6

def sender(udp):
    time.sleep(.3) # Bind pidfd monitoring before the first WBC output.
    motors=[(1 if i<29 else 0,q,0,0,40 if i<29 else 0,3 if i<29 else 0,0) for i,q in enumerate(positions())]
    raw=struct.pack('<BB2x',0,5)+b''.join(struct.pack('<B3x5fI',*m) for m in motors)+struct.pack('<4I',0,0,0,0)
    checksum=crc(raw);seq=0
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
        while True:
            seq+=1
            payload=struct.pack('<4sHHQQQBBBB4II',b'LCU1',1,0,42,seq,time.monotonic_ns(),0,5,35,0,0,0,0,0,checksum)+b''.join(struct.pack('<B5fI',*m) for m in motors)
            assert len(payload)==931
            s.sendto(payload,('127.0.0.1',udp));time.sleep(.002)

def main():
    p=argparse.ArgumentParser();p.add_argument('--binary',type=Path);p.add_argument('--output',type=Path);p.add_argument('--sender',type=int);a=p.parse_args()
    if a.sender:return sender(a.sender)
    assert os.environ.get('ROS_DOMAIN_ID')=='94'
    assert os.environ.get('RMW_IMPLEMENTATION')=='rmw_cyclonedds_cpp'
    assert '<NetworkInterfaceAddress>lo</NetworkInterfaceAddress>' in os.environ.get('CYCLONEDDS_URI','')
    a.output.mkdir(parents=True,exist_ok=False)
    results=[]
    for case in ['sigterm','sigkill','intentional_stop','state_loss','moving_sigkill','overspeed_sigkill']:
        folder=a.output/case;folder.mkdir();stop=threading.Event();feedback_stop=threading.Event();speed_recovered=threading.Event()
        ctx=zmq.Context();pub=ctx.socket(zmq.PUB);primary=port(socket.SOCK_STREAM);pub.bind('tcp://127.0.0.1:'+str(primary))
        udp=port(socket.SOCK_DGRAM);sock=folder/'control.sock';folder.chmod(0o700)
        def feedback():
            tick=0
            while not stop.wait(.002):
                if feedback_stop.is_set():continue
                tick+=2
                body=dict(motor_state=[dict(q=q,dq=(20. if not speed_recovered.is_set() else 9.) if case=='overspeed_sigkill' and i==3 else (4. if case=='moving_sigkill' and i==3 else 0.),motorstate=0) for i,q in enumerate(positions())],imu_state=dict(rpy=[0.,0.,0.]))
                pub.send(msgpack.packb(dict(source='primary',topic='rt/lowstate',captured=time.monotonic(),tick=tick,payload=msgpack.packb(body,use_bin_type=True)),use_bin_type=True))
        thread=threading.Thread(target=feedback);thread.start()
        log=(folder/'process.log').open('w');child=None;client=None
        bridge=subprocess.Popen([str(a.binary),'--udp-port',str(udp),'--cmd-port',str(port(socket.SOCK_STREAM)),'--primary-port',str(primary),'--standby-port',str(port(socket.SOCK_STREAM)),'--logs',str(folder/'trace'),'--experimental-protection-socket',str(sock),'--protection-voice'],stdout=log,stderr=subprocess.STDOUT)
        try:
            end=time.monotonic()+8
            while not sock.exists():
                assert bridge.poll() is None,(folder/'process.log').read_text()
                assert time.monotonic()<end,'bridge admission timed out';time.sleep(.02)
            client=ProtectionClient(sock);client.reset();generation=client.generation
            child=subprocess.Popen([sys.executable,__file__,'--sender',str(udp)])
            client.arm(child.pid)
            assert request(sock,'status')['watching']
            end=time.monotonic()+3
            while request(sock,'status')['stream']!=42:
                assert time.monotonic()<end;time.sleep(.01)
            time.sleep(.1)
            assert request(sock,'status')['armed']
            at=time.monotonic_ns()
            if case=='intentional_stop':client.cancel()
            if case=='state_loss':feedback_stop.set();time.sleep(.05)
            child.send_signal(signal.SIGKILL if case in ('sigkill','moving_sigkill','overspeed_sigkill') else signal.SIGTERM);child.wait(timeout=2)
            end=time.monotonic()+2
            while True:
                state=request(sock,'status')
                if (case=='intentional_stop' and state['estopped']) or (case!='intentional_stop' and state['fenced']):break
                assert time.monotonic()<end;time.sleep(.005)
            if case=='intentional_stop':assert state['phase']==0
            elif case=='state_loss':assert state['phase']==3
            else:assert state['phase']==1
            recovery=None
            if case=='overspeed_sigkill':
                assert state['braking_joints']==1 and state['speed_brake_events']==1,state
                speed_recovered.set()
                end=time.monotonic()+1
                while True:
                    recovery=request(sock,'status')
                    if recovery['braking_joints']==0:break
                    assert time.monotonic()<end;time.sleep(.005)
                assert recovery['phase']==1 and recovery['speed_brake_events']==1,recovery
            # A replacement WBC cannot recover the stream after protection owns it.
            replacement=subprocess.Popen([sys.executable,__file__,'--sender',str(udp)])
            try:
                time.sleep(.1);after=request(sock,'status');assert after['fenced'] and not after['armed'];assert after['phase']==state['phase']
            finally:replacement.terminate();replacement.wait(timeout=2)
            client.cancel()
            time.sleep(6.1 if case!='intentional_stop' else 1.1) # Warning also covers rejected protective motion.
            events=[]
            for f in (folder/'trace').glob('*/events.jsonl'):
                for line in f.read_text().splitlines():
                    try:events.append(json.loads(line))
                    except ValueError:pass
            transition=next((e['sample']['at_ns'] for e in events if e['event']=='protection_transition'),None)
            voice=next(e['protection_voice'] for e in reversed(events) if 'protection_voice' in e)
            expected_voice=0 if case=='intentional_stop' else generation
            assert voice['requested_generation']==expected_voice and voice['published_generation']==expected_voice and not voice['failed'],voice
            assert voice['repetitions']==3 and 0<voice['single_duration_s']<=2
            results.append(dict(case=case,status=state,recovery=recovery,voice=voice,first_transition_ms=None if transition is None else (transition-at)/1e6,physical=False))
        finally:
            if client:client.close()
            if child and child.poll() is None:child.kill();child.wait()
            bridge.terminate();bridge.wait(timeout=5);log.close();stop.set();thread.join();pub.close(0);ctx.term()
    (a.output/'report.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
if __name__=='__main__':main()
