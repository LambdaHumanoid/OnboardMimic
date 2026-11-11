import copy,json,math,unittest
from pathlib import Path
from onboardmimic.inputs.pico.receiver import Monitor,SDKReader,ROLES
from onboardmimic.inputs.pico.patch_sdk import patch

def config():return {'roles':dict(zip(ROLES,['L','R','W','LA','RA']))}
def frame(stamp=100):
 p=[0,1,0,0,0,0,1]
 return dict(head=dict(timestamp_ns=stamp,pose=p),body=dict(timestamp_ns=stamp,poses=[p[:] for _ in range(24)]),motion=dict(timestamp_ns=stamp,serials=['L','R','W','LA','RA'],poses=[p[:] for _ in range(5)]))

class ReceiverTest(unittest.TestCase):
 def test_five_serial_roles_not_array_order(self):
  m=Monitor(config());x=frame();x['motion']['serials'].reverse();s=m.ingest(x,0)
  self.assertTrue(s['five_tracker_capture_ready']);self.assertEqual(set(s['role_poses']),set(ROLES));self.assertFalse(s['robot_output_enabled']);self.assertFalse(s['calibrated'])
 def test_duplicate_polls_do_not_refresh(self):
  m=Monitor(config());m.ingest(frame(),0);s=m.ingest(frame(),.3)
  self.assertFalse(s['five_tracker_capture_ready']);self.assertIsNone(s['streams']['motion']['payload'])
 def test_streams_age_independently(self):
  m=Monitor(config());m.ingest(frame(),0);x=frame();x['body']['timestamp_ns']=200;s=m.ingest(x,.3)
  self.assertTrue(s['body_capture_ready']);self.assertFalse(s['five_tracker_capture_ready'])
 def test_head_updates_cannot_keep_body_fresh(self):
  m=Monitor(config());m.ingest(frame(100),0)
  for i in range(1,31):
   x=frame(100);x['head']['timestamp_ns']=100+i;s=m.ingest(x,i*.01)
  self.assertTrue(s['streams']['head']['fresh']);self.assertFalse(s['body_capture_ready'])
  self.assertIsNone(s['streams']['body']['payload'])
 def test_full_body_does_not_require_independent_trackers(self):
  x=frame();x['motion']={'timestamp_ns':0,'serials':[],'poses':[]}
  s=Monitor(config()).ingest(x,0)
  self.assertTrue(s['body_capture_ready']);self.assertFalse(s['five_tracker_capture_ready'])
 def test_body_packet_loss_and_recovery(self):
  m=Monitor(config());m.ingest(frame(100),0)
  self.assertFalse(m.status(.251)['body_capture_ready'])
  self.assertTrue(m.ingest(frame(200),.3)['body_capture_ready'])
 def test_three_tracker_limit_exposed(self):
  m=Monitor(config());x=frame();x['motion']['poses']=x['motion']['poses'][:3];x['motion']['serials']=x['motion']['serials'][:3]
  s=m.ingest(x,0);self.assertFalse(s['five_tracker_capture_ready']);self.assertEqual(s['missing_roles'],['left_ankle','right_ankle'])
 def test_duplicate_serial_and_bad_pose(self):
  for bad in ('serial','nan','quaternion'):
   x=frame()
   if bad=='serial':x['motion']['serials'][0]='R'
   if bad=='nan':x['motion']['poses'][0][0]=float('nan')
   if bad=='quaternion':x['motion']['poses'][0][3:]=[0,0,0,0]
   self.assertFalse(Monitor(config()).ingest(x,0)['five_tracker_capture_ready'])
 def test_regression_latched_even_after_missing_data(self):
  m=Monitor(config());m.ingest(frame(200),0);self.assertFalse(m.ingest(frame(100),.1)['five_tracker_capture_ready'])
  m.ingest({},.2);self.assertFalse(m.ingest(frame(300),.3)['five_tracker_capture_ready'])
 def test_no_role_guessing(self):
  c={'roles':dict.fromkeys(ROLES,'')};s=Monitor(c).ingest(frame(),0);self.assertEqual(len(s['missing_roles']),5)
  c['roles']['left_wrist']=c['roles']['right_wrist']='same'
  with self.assertRaises(ValueError):Monitor(c)
 def test_body_shape_invalid(self):
  x=frame();x['body']['poses'].pop();self.assertFalse(Monitor(config()).ingest(x,0)['body_capture_ready'])
 def test_fps_counts_unique_device_frames(self):
  m=Monitor(config())
  for i in range(11):m.ingest(frame(100+i),i*.02)
  self.assertAlmostEqual(m.status(.2)['streams']['motion']['unique_frame_hz'],50)
 def test_sdk_torn_read_rejected(self):
  class SDK:pass
  r=SDKReader(SDK());it=iter([1,2,2,3,3,4])
  with self.assertRaises(ValueError):r.coherent(lambda:next(it),lambda:{'poses':[]})
 def test_sdk_torn_read_retries_with_new_payload(self):
  r=SDKReader(object());stamps=iter([1,2,2,2]);payloads=iter([{'poses':['old']},{'poses':['new']}])
  self.assertEqual(r.coherent(lambda:next(stamps),lambda:next(payloads)),{'timestamp_ns':2,'poses':['new']})
 def test_sdk_getter_error_not_hidden_by_retry(self):
  r=SDKReader(object())
  def broken():raise RuntimeError('SDK unavailable')
  with self.assertRaises(RuntimeError):r.coherent(lambda:1,broken)
 def test_patch_idempotent_and_pinned(self):
  src=Path(__file__).resolve().parents[2].joinpath('onboardmimic/inputs/pico/reference').joinpath('xrt_py_bindings.cpp').read_text();s=patch(src)
  self.assertIn('get_head_timestamp_ns',s);self.assertIn('joints.size()), 16)',s);self.assertEqual(s,patch(s));self.assertEqual(s,patch(s.replace('// OnboardMimic:', '// Earlier deployment:')))
  with self.assertRaises(ValueError):patch('changed upstream')


if __name__=='__main__':unittest.main()
