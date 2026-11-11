"""Domain94 / loopback / non-motor-topic verification only."""
import os,sys,time,json,socket,struct,threading,subprocess,argparse
from pathlib import Path
assert os.environ['ROS_DOMAIN_ID']=='94'
assert os.environ['RMW_IMPLEMENTATION']=='rmw_cyclonedds_cpp'
assert '<NetworkInterfaceAddress>lo</NetworkInterfaceAddress>' in os.environ['CYCLONEDDS_URI']
# Source ROS2 and expose the Unitree Python SDK through PYTHONPATH first.
from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
from unitree_sdk2py.utils.crc import CRC
import rclpy
from rclpy.qos import QoSProfile,ReliabilityPolicy
from unitree_hg.msg import LowCmd
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary',required=True,type=Path)
parser.add_argument('--topic',default='/onboardmimic/diagnostics/bridge_latency',choices=['/onboardmimic/diagnostics/bridge_latency'])
parser.add_argument('--output',required=True,type=Path)
parser.add_argument('--contender',type=Path,help='Other deployment bridge; defaults to same binary')
args=parser.parse_args();binary_path=args.binary.resolve()
assert binary_path.is_file()
OUT=args.output.resolve();OUT.mkdir(parents=True,exist_ok=False)
for p in Path('/proc').glob('[0-9]*/cmdline'):
 try:
  argv=p.read_bytes().split(b'\0')
  assert not any(Path(a.decode(errors='replace')).name in ('g1_deploy_onnx_ref','g1_deploy') for a in argv if a), 'Controller active'
 except FileNotFoundError:pass
rclpy.init();node=rclpy.create_node('authority_isolated_test')
results=[]
def port(udp=False):
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as s:
  s.bind(('127.0.0.1',0));return s.getsockname()[1]
def events(folder):
 files=list(folder.glob('trace/*/events.jsonl'))
 if not files:return []
 lines=files[0].read_text().splitlines();out=[]
 for line in lines:
  try:out.append(json.loads(line))
  except ValueError:pass
 return out
from cyclonedds.domain import DomainParticipant
from cyclonedds.topic import Topic
from cyclonedds.pub import DataWriter
from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_
participant=DomainParticipant(94)
for binary,topic,flavor in [(binary_path.name,args.topic,f) for f in ('ros','sdk')]:
 folder=OUT/(binary+'-'+flavor);folder.mkdir()
 if flavor=='ros':
  pub=node.create_publisher(LowCmd,topic,QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT));msg=LowCmd();publish=pub.publish
 else:
  sdk_topic=Topic(participant,'rt'+topic,LowCmd_);pub=DataWriter(participant,sdk_topic);msg=unitree_hg_msg_dds__LowCmd_();publish=pub.write
 # All motor mode/gain/torque fields stay zero on the diagnostic topic.
 flag=threading.Event();end=threading.Event()
 def foreign():
  while not end.wait(.005):
   if flag.is_set():publish(msg)
 t=threading.Thread(target=foreign);t.start();children=[];logs=[];sender_stop=threading.Event();sender=None
 def launch(name,contender=False):
  f=folder/name;f.mkdir();u=port(True);c=port();a=port();b=port();log=(f/'process.log').open('w');logs.append(log)
  p=subprocess.Popen([str(args.contender.resolve() if contender and args.contender else binary_path),'--udp-port',str(u),'--cmd-port',str(c),'--primary-port',str(a),'--standby-port',str(b),'--logs',str(f/'trace')],stdout=log,stderr=subprocess.STDOUT)
  children.append(p);return p,u,f
 try:
  flag.set();p,u,f=launch('foreign-before-start');p.wait(timeout=8)
  assert p.returncode!=0 and any(x.get('event')=='authority_startup_conflict' and x['dds_writes']==0 for x in events(f)),events(f)
  flag.clear();time.sleep(.3)
  p,u,f=launch('single-writer-then-conflict');deadline=time.monotonic()+8
  while not any(x.get('event')=='native_bridge_start' for x in events(f)):
   assert p.poll() is None, (f/'process.log').read_text()
   assert time.monotonic()<deadline,'Bridge never ready'
   time.sleep(.05)
  c=unitree_hg_msg_dds__LowCmd_();c.mode_pr=0;c.mode_machine=5
  for m in c.motor_cmd:m.mode=0;m.q=0.;m.dq=0.;m.kp=0.;m.kd=0.;m.tau=0.;m.reserve=0
  c.crc=CRC().Crc(c)
  payload=bytearray(931);struct.pack_into('<4sHHQQQBBBB4II',payload,0,b'LCU1',1,0,9911,1,time.monotonic_ns(),0,5,35,0,*c.reserve,c.crc)
  def send():
   sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);seq=0;deadline=time.monotonic()
   while not sender_stop.is_set():
    seq+=1;struct.pack_into('<QQQ',payload,8,9911,seq,time.monotonic_ns());sock.sendto(payload,('127.0.0.1',u));deadline+=.002;sender_stop.wait(max(0,deadline-time.monotonic()))
   sock.close()
  sender=threading.Thread(target=send);sender.start();time.sleep(10)
  assert p.poll() is None,(f/'process.log').read_text()
  stats=[x for x in events(f) if x.get('event')=='native_bridge_stats'];assert stats[-1]['dds_writes']>2000,stats[-1]
  q,_,g=launch('later-local-contender',contender=True);q.wait(timeout=5)
  assert q.returncode==1 and 'CONTROL_OWNER_BUSY' in (g/'process.log').read_text()
  assert p.poll() is None,'Incumbent interrupted by contender'
  before=stats[-1]['dds_writes'];flag.set();time.sleep(3)
  assert p.poll() is None,'Incumbent interrupted by external writer'
  after=[x for x in events(f) if x.get('event')=='native_bridge_stats'][-1]
  assert after['dds_writes']>before+800 and after['foreign_lowcmd_count']>0,after
  assert after['state'] not in ('failing','latched'),after
  assert after['max_gap_ns'][1]<100000000,after
  assert any(x.get('event')=='foreign_lowcmd_observed' and x['action']=='keep_current_owner' for x in events(f))
  flag.clear();sender_stop.set();sender.join(2);p.terminate();p.wait(timeout=5)
  q,_,g=launch('explicit-successor',contender=True);deadline=time.monotonic()+8
  while not any(x.get('event')=='native_bridge_start' for x in events(g)):
   assert q.poll() is None,(g/'process.log').read_text()
   assert time.monotonic()<deadline,'Successor never ready'
   time.sleep(.05)
  q.terminate();q.wait(timeout=5)
  results.append({'binary':binary,'foreign_flavor':flavor,'passed':True,'single_writer_stats':stats[-1],
    'with_foreign_stats':after,'startup_foreign_rejected':True,'later_local_contender_rejected':True,
    'incumbent_continued':True,'explicit_successor_admitted':True,'physical':False})

 finally:
  sender_stop.set()
  if sender:sender.join(2)
  end.set();t.join(2)
  for p in children:
   if p.poll() is None:p.terminate();p.wait(timeout=5)
  for f in logs:f.close()
  if flavor=='ros':node.destroy_publisher(pub)
  else:del pub,sdk_topic
node.destroy_node();rclpy.shutdown()
(OUT/'report.json').write_text(json.dumps({'passed':True,'cases':results},indent=2)+'\n');print(json.dumps(results,indent=2))
