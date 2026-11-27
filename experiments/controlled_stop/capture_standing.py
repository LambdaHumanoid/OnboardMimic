"""Read-only normal-SONIC capture. No controller actions or LowCmd publisher."""
import json,time,socket,threading,os,argparse
from pathlib import Path
import urllib.request
import zmq,msgpack

def status():
 token=(Path.home()/'.config/onboardmimic/http.token').read_text().strip()
 req=urllib.request.Request('http://127.0.0.1:18921/api/controller',headers={'Authorization':'Bearer '+token})
 s=json.load(urllib.request.urlopen(req,timeout=2))
 return {k:s.get(k) for k in ('state','pids','stationary','input_mode','telemetry','workflow_error')}

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=float,default=10);p.add_argument('--socket',type=Path,required=True);a=p.parse_args()
 if not 1<=a.seconds<=30:p.error('capture must be 1..30 seconds')
 os.umask(0o077);a.output.mkdir(parents=True,exist_ok=False)
 before=status();(a.output/'before.json').write_text(json.dumps(before));print('BEFORE',before['state'],before['pids'],before['stationary'],flush=True)
 if before['state']!='active' or before['input_mode']!='planner':
  (a.output/'manifest.json').write_text(json.dumps(dict(cancelled=True,reason='not active Planner',control_actions=0)))
  raise SystemExit('Not active Planner; capture cancelled without control changes')
 end=time.monotonic()+a.seconds;errors=[];count=[0,0]
 def commands():
  with (a.output/'commands.jsonl').open('w') as f:
   while time.monotonic()<end:
    try:
     with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as s:
      s.settimeout(.05);s.connect(str(a.socket));s.sendall(b'{"action":"snapshot"}');value=json.loads(s.recv(16384))
     f.write(json.dumps(dict(received=time.monotonic(),value=value))+'\n');count[1]+=1
    except Exception as e:
     if len(errors)<5:errors.append(str(e))
    time.sleep(.05)
 thread=threading.Thread(target=commands);thread.start()
 ctx=zmq.Context();sub=ctx.socket(zmq.SUB);sub.setsockopt(zmq.SUBSCRIBE,b'');sub.setsockopt(zmq.RCVHWM,500);sub.connect('tcp://127.0.0.1:16665')
 packer=msgpack.Packer(use_bin_type=True)
 with (a.output/'feedback.msgpack').open('wb') as f:
  while time.monotonic()<end:
   if not sub.poll(10):continue
   f.write(packer.pack(dict(received=time.monotonic(),envelope=sub.recv())));count[0]+=1
 sub.close(0);ctx.term();thread.join();after=status();(a.output/'after.json').write_text(json.dumps(after))
 (a.output/'manifest.json').write_text(json.dumps(dict(read_only=True,counts=count,command_capture_errors=errors,control_actions=0,seconds=a.seconds)))
 print('AFTER',after['state'],after['pids'],after['stationary']);print('OUTPUT',a.output);print('COUNTS',count,'ERRORS',errors)
if __name__=='__main__':main()
