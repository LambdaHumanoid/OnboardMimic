import io,json,threading,unittest,sys,tempfile
from pathlib import Path
from unittest.mock import Mock
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from onboardmimic.console.server import Console,make_server

class ConsoleTest(unittest.TestCase):
 def setUp(self):
  self.backend=Mock();self.backend.status.return_value=dict(state='stopped',mode='onboard_real',pids=[],prepare_available=True)
  self.runner=Mock(return_value=Mock(returncode=0,stdout='ActiveState=inactive\nResult=success\n',stderr=''))
  self.c=Console(self.backend,Path(__file__).resolve().parents[2]/'onboardmimic/console/static',self.runner)
  self.tmp=tempfile.TemporaryDirectory();self.c.connect_status=Path(self.tmp.name)/'connect-status.json'
  self.s=make_server(self.c);self.t=threading.Thread(target=self.s.serve_forever,daemon=True);self.t.start();self.url='http://127.0.0.1:'+str(self.s.server_port)
 def tearDown(self):self.s.shutdown();self.s.server_close();self.t.join();self.tmp.cleanup()
 def post(self,path,data,token=True):
  headers={'Content-Type':'application/json'}
  if token:headers['X-Control-Token']=self.c.token
  return urlopen(Request(self.url+path,data=json.dumps(data).encode(),headers=headers),timeout=3)
 def test_get_never_connects_or_moves(self):
  for path in ('/api/session','/api/controller','/','/real'):
   with urlopen(self.url+path) as r:content=r.read()
  self.backend.command.assert_not_called();self.backend.control.assert_not_called()
  self.assertTrue(all(c.args[0][2]=='show' for c in self.runner.call_args_list))
  self.assertNotIn(b'/simulation',content)
  self.assertIn(b'/app.js',content)
 def test_control_routes_require_authorization(self):
  with urlopen(self.url+'/api/controller') as r:
   self.assertEqual(json.load(r)['state'],'stopped')
  for route in ('/api/controller','/api/control'):
   with self.subTest(route=route),self.assertRaises(HTTPError) as error:
    self.post(route,{'action':'estop'},token=False)
   self.assertEqual(error.exception.code,403)
   error.exception.close()
  self.backend.command.assert_not_called();self.backend.control.assert_not_called()
 def test_platform_catalog_is_read_only_and_never_claims_hot_switch_support(self):
  with urlopen(self.url+'/api/platform') as response:catalog=json.load(response)
  self.assertEqual(catalog['schema_version'],1)
  self.assertEqual(catalog['selected'],{'robot_id':'unitree_g1','backend_id':'sonic'})
  self.assertIn(catalog['selected'],catalog['pairs'])
  self.assertEqual(catalog['switching'],{'supported':False,'reason':'service_configuration_required'})
  self.runner.assert_not_called();self.backend.status.assert_not_called()
  self.backend.command.assert_not_called();self.backend.control.assert_not_called()
 def test_platform_selection_cannot_mutate_runtime(self):
  for route in ('/api/platform','/api/platform/select'):
   with self.assertRaises(HTTPError) as error:
    self.post(route,{'robot_id':'unitree_g1','backend_id':'sonic'})
   self.assertEqual(error.exception.code,404);error.exception.close()
  self.runner.assert_not_called();self.backend.command.assert_not_called();self.backend.control.assert_not_called()
 def test_mobile_routes_are_read_only(self):
  for path in ('/remote','/real/remote','/mobile.css'):
   with urlopen(self.url+path) as response:self.assertEqual(response.status,200)
  self.backend.command.assert_not_called();self.backend.control.assert_not_called()
 def test_skeleton_readonly_fresh_stale_and_invalid(self):
  import time
  with tempfile.TemporaryDirectory() as tmp:
   self.c.pico_runs=Path(tmp);run=Path(tmp)/'sample';run.mkdir();p=run/'status.json'
   pose=[0,1,0,0,0,0,1]
   data=dict(updated_at_unix=time.time(),receiver_running=True,data_source='sdk',streams={'body':dict(fresh=True,age_ms=0,unique_frame_hz=80,payload=[pose]*24,source_timestamp_ns=123)})
   p.write_text(json.dumps(data))
   with urlopen(self.url+'/api/pico/skeleton') as r:x=json.load(r)
   self.assertTrue(x['available']);self.assertEqual(len(x['joints']),24)
   self.assertFalse(x['robot_output_enabled']);self.runner.assert_not_called();self.backend.status.assert_not_called()
   data['updated_at_unix']=time.time()-1;p.write_text(json.dumps(data));self.assertFalse(self.c.pico_skeleton()['available'])
   data['updated_at_unix']=time.time();data['streams']['body']['payload'][0]=[0]*7;p.write_text(json.dumps(data));self.assertEqual(self.c.pico_skeleton()['joints'],[])
   data['data_source']='replay';p.write_text(json.dumps(data));self.assertFalse(self.c.pico_skeleton()['available'])
  self.backend.command.assert_not_called();self.backend.control.assert_not_called()
 def test_connect_requires_explicit_authorized_post(self):
  with self.assertRaises(HTTPError) as e:self.post('/api/controller',{'action':'connect_services'},False)
  self.assertEqual(e.exception.code,403);self.runner.assert_not_called()
  self.post('/api/controller',{'action':'connect_services'}).close()
  self.assertTrue(any('restart' in c.args[0] for c in self.runner.call_args_list))
  self.backend.command.assert_not_called()
 def test_prepare_before_setup_refused(self):
  with self.assertRaises(HTTPError):self.post('/api/controller',{'action':'prepare_pose'})
  self.backend.command.assert_not_called()
 def unit_states(self,setup='active',bridge='active',pid=123):
  self.c.connect_status.write_text(json.dumps({'phase':'ready','bridge_pid':pid}))
  def run(args,**kwargs):
   value=('ActiveState='+bridge+'\nMainPID='+str(pid)+'\n') if 'onboardmimic-bridge.service' in args else ('ActiveState='+setup+'\nResult=success\n')
   return Mock(returncode=0,stdout=value,stderr='')
  self.runner.side_effect=run
 def test_successful_oneshot_with_dead_bridge_is_not_connected(self):
  self.unit_states(bridge='failed',pid=0)
  s=self.c.status();self.assertFalse(s['setup_ready']);self.assertFalse(s['prepare_available'])
  self.assertIn('桥接未就绪',s['setup_error'])
  for action in ('prepare_pose','start_policy'):
   with self.assertRaises(HTTPError):self.post('/api/controller',{'action':action})
  self.backend.command.assert_not_called()
 def test_waits_for_connect_completion_then_allows_manual_prepare(self):
  self.unit_states(setup='activating')
  self.assertFalse(self.c.status()['prepare_available'])
  self.unit_states()
  self.assertTrue(self.c.status()['prepare_available'])
  self.backend.command.return_value={'ok':True}
  self.post('/api/controller',{'action':'prepare_pose'}).close()
  self.backend.command.assert_called_once_with('prepare_pose')
 def test_replaced_bridge_does_not_reuse_previous_success(self):
  self.unit_states(pid=456)
  self.c.connect_status.write_text(json.dumps({'phase':'ready','bridge_pid':123}))
  self.assertFalse(self.c.status()['setup_ready'])
  self.assertFalse(self.c.status()['prepare_available'])
 def test_lateral_allowed_and_removed_actions_rejected(self):
  self.backend.control.return_value={'ok':True}
  for move in ('left','right'):
   self.post('/api/control',{'move':move}).close()
   self.backend.control.assert_called_with({'move':move})
  self.backend.control.reset_mock()
  for payload in ({'gesture':'wave'},{'run':True},{'action_id':'old'}):
   with self.assertRaises(HTTPError):self.post('/api/control',payload)
  with self.assertRaises(HTTPError):self.post('/api/unknown/control',{'move':'forward'})
  self.backend.control.assert_not_called()
 def test_active_robot_cannot_reconnect(self):
  self.backend.status.return_value.update(state='active',pids=[123])
  with self.assertRaises(HTTPError):self.post('/api/controller',{'action':'connect_services'})
  self.assertFalse(any('restart' in c.args[0] for c in self.runner.call_args_list))
 def test_forward_keeps_speed(self):
  self.backend.control.return_value={'ok':True}
  x={'move':'forward','speed':.4,'client':'test','seq':1}
  self.post('/api/control',x).close();self.backend.control.assert_called_once_with(x)
 def test_running_mode_switch_uses_gateway_and_blocks_planner(self):
  self.backend.status.return_value.update(state='active',pose_adapter_ready=True,input_mode_switch_available=True,input_mode='planner')
  def command(action):
   self.backend.status.return_value['input_mode']='pose' if action=='select_pose' else 'planner'
  self.backend.command.side_effect=command
  with self.post('/api/controller',{'action':'select_input_mode','input_mode':'pose'}) as r:state=json.load(r)
  self.assertEqual(state['input_mode'],'pose');self.backend.command.assert_called_once_with('select_pose')
  with self.assertRaises(HTTPError):self.post('/api/control',{'move':'forward'})
  self.backend.control.return_value={'ok':True};self.post('/api/control',{'stop':True}).close()
  self.post('/api/controller',{'action':'select_input_mode','input_mode':'planner'}).close()
  self.assertEqual(self.c.input_mode,'planner')
 def test_mode_switch_refuses_running_unknown_and_bad_modes(self):
  for state in ('active','prepared','preparing','loading','error'):
   self.backend.status.return_value['state']=state
   with self.assertRaises(HTTPError):self.post('/api/controller',{'action':'select_input_mode','input_mode':'pose'})
  self.backend.status.return_value['state']='stopped'
  for mode in ('unknown',None):
   with self.assertRaises(HTTPError):self.post('/api/controller',{'action':'select_input_mode','input_mode':mode})
  self.assertEqual(self.c.input_mode,'planner');self.backend.command.assert_not_called()
 def test_pose_preserves_estop(self):
  self.c.input_mode='pose';self.backend.command.return_value={'ok':True}
  self.post('/api/controller',{'action':'estop'}).close()
  self.backend.command.assert_called_once_with('estop')
 def test_pico_expired_replay_and_live_status(self):
  import time
  with tempfile.TemporaryDirectory() as d:
   self.c.pico_runs=Path(d);run=Path(d)/'capture';run.mkdir();p=run/'status.json'
   sample=dict(updated_at_unix=time.time(),receiver_running=True,data_source='sdk',streams={'head':{'fresh':True}},visible_tracker_serials=['a','b'])
   p.write_text(json.dumps(sample));self.assertTrue(self.c.pico_status()['connected'])
   self.assertFalse(self.c.pico_status()['ready'])
   sample['data_source']='replay';p.write_text(json.dumps(sample));self.assertFalse(self.c.pico_status()['connected'])
   sample.update(data_source='sdk',updated_at_unix=time.time()-10);p.write_text(json.dumps(sample));self.assertFalse(self.c.pico_status()['connected'])
if __name__=='__main__':unittest.main()
