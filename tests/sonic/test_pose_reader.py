import json,tempfile,time,unittest
import numpy as np
from onboardmimic.backends.sonic.pico.pose_reader import PoseReader
from onboardmimic.backends.sonic.pico.pose_mapping import map_body,UP,BASE,FLIP

class PoseReaderTest(unittest.TestCase):
 def setUp(self):
  self.now=0.;self.r=PoseReader('/unused',clock=lambda:self.now)
  self.p=np.zeros((24,7));self.p[:,3:]=(UP.inv()*BASE*FLIP.inv()).as_quat()
 def feed(self,i,**changes):
  self.now=i*.02;b=dict(fresh=True,age_ms=0,source_timestamp_ns=1000000000+i*20000000,payload=self.p.tolist())
  data=dict(updated_at_unix=100.,receiver_running=True,data_source='sdk',streams={'body':b});data.update(changes);self.r.feed(data,wall=100.)
 def test_mapping_and_packet(self):
  for i in range(8):self.feed(i)
  s=self.r.snapshot();self.assertTrue(s['ready']);wire=s['packet'];self.assertTrue(wire.startswith(b'pose'))
  hdr=json.loads(wire[4:1284].rstrip(b'\x00 '));self.assertEqual(hdr['v'],3)
  self.assertEqual({x['name']:x['shape'] for x in hdr['fields']}['smpl_joints'],[5,24,3])
 def test_duplicate_does_not_renew(self):
  for i in range(8):self.feed(i)
  self.feed(7);self.now+=.26;self.assertFalse(self.r.snapshot()['ready'])
 def test_stream_gap_clears_window(self):
  for i in range(8):self.feed(i)
  self.feed(30);self.assertFalse(self.r.snapshot()['ready'])
 def test_replay_and_invalid_rejected(self):
  for i in range(8):self.feed(i)
  self.feed(8,data_source='replay');self.assertFalse(self.r.snapshot()['ready'])
  with self.assertRaises(ValueError):map_body([[0]*7]*24)
 def test_clock_regression_latched(self):
  for i in range(8):self.feed(i)
  self.feed(1);self.feed(10);self.assertFalse(self.r.snapshot()['ready'])
 def test_neutral_root_and_wrist_contract(self):
  x=map_body(self.p);np.testing.assert_allclose(abs(x['body_quat_w']),[1,0,0,0],atol=1e-6)
  self.assertEqual(x['joint_pos'].shape,(29,));np.testing.assert_allclose(x['joint_pos'][:23],0)
if __name__=='__main__':unittest.main()
