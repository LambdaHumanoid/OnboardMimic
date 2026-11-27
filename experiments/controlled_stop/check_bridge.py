"""Diagnostic-domain bridge only; never uses any physical-control port."""
import json,socket,threading,time
from pathlib import Path
import numpy as np,zmq,msgpack
from wire import Encoder
from control_api import ipc
ROOT=Path(__file__).resolve().parent
ctx=zmq.Context();pub=ctx.socket(zmq.PUB);pub.bind('tcp://127.0.0.1:19665');udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
encoder=Encoder();cmd=np.zeros((29,5));cmd[:,3]=30;cmd[:,4]=4
path=ROOT/'diagnostic.sock';tick=0

def send(stream,seconds):
 global tick
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  tick+=1;at=time.monotonic();state=dict(motor_state=[dict(q=0.) for _ in range(35)])
  pub.send(msgpack.packb(dict(source='primary',topic='rt/lowstate',captured=at,tick=tick,payload=msgpack.packb(state,use_bin_type=True)),use_bin_type=True))
  udp.sendto(encoder.encode(cmd,0,stream),('127.0.0.1',19663));time.sleep(.004)
try:
 time.sleep(.2);send(111,.2);first=ipc(path,'snapshot');assert first['state']=='active' and first['stream']==111,first
 claimed=ipc(path,'claim',expected_stream=111,new_stream=222)
 cmd=np.array(claimed['command']);send(222,.04);second=ipc(path,'snapshot');assert second['stream']==222 and second['state']=='active',second
 send(111,.02);third=ipc(path,'snapshot');assert third['stream']==222,'old stream stole ownership'
 time.sleep(.3);last=ipc(path,'snapshot');assert last['state']=='latched',last
 assert all(row[3]==0 and row[4]==8 for row in last['command'])
 print(json.dumps(dict(pass_test=True,first_stream=first['stream'],handoff_stream=second['stream'],old_stream_rejected=True,timeout=last['state'],damping_kd=8,physical=False)))
finally:pub.close(0);ctx.term();udp.close()
