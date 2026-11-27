import unittest
import numpy as np
from scipy.spatial.transform import Rotation as R
from onboardmimic.backends.sonic.pico.pose_mapping import map_body,UP,BASE,FLIP
from onboardmimic.backends.sonic.pico.pose_consistency import compare_arms,ArmAdmission
from onboardmimic.backends.sonic.pico.pose_reader import PoseReader

class ConsistencyTest(unittest.TestCase):
    def sample(self):
        p=np.zeros((24,7));p[:,3:]=(UP.inv()*BASE*FLIP.inv()).as_quat()
        mapped=map_body(p)
        q=mapped['body_quat_w'];orient=R.from_quat(q[[1,2,3,0]])
        p[:,:3]=(UP.inv()*orient).apply(mapped['smpl_joints'])*1.3+[2,3,4]
        return p,mapped
    def test_matching_directions_ignore_scale_translation_and_global_rotation(self):
        p,m=self.sample();self.assertTrue(compare_arms(p,m)['consistent'])
        rotate=R.from_euler('xyz',[20,40,70],degrees=True)
        p[:,:3]=rotate.apply(p[:,:3]);p[:,3:]=(rotate*R.from_quat(p[:,3:])).as_quat()
        self.assertTrue(compare_arms(p,map_body(p))['consistent'])
    def test_each_arm_direction_checked(self):
        for parent,child in [(16,18),(18,20),(17,19),(19,21)]:
            p,m=self.sample();p[child,:3]=2*p[parent,:3]-p[child,:3]
            self.assertFalse(compare_arms(p,m)['consistent'])
    def test_missing_positions_fail_closed(self):
        p,m=self.sample();p[18,:3]=p[16,:3]
        self.assertFalse(compare_arms(p,m)['valid'])
    def test_continuous_dwell_and_recovery(self):
        gate=ArmAdmission();good=dict(valid=True,consistent=True);bad=dict(valid=True,consistent=False)
        gate.update(good,0);gate.update(good,.2);self.assertFalse(gate.result['ready'])
        gate.update(bad,.21);gate.update(good,.22);gate.update(good,.51);self.assertFalse(gate.result['ready'])
        gate.update(good,.52);self.assertTrue(gate.result['ready'])
        gate.update(bad,.6);self.assertEqual(gate.result['state'],'pending')
        gate.update(bad,.9);self.assertEqual(gate.result['state'],'blocked')
    def test_reader_requires_unique_fresh_consistent_frames(self):
        p,_=self.sample();now=[0.];reader=PoseReader('/unused',clock=lambda:now[0])
        def feed(i):
            now[0]=i*.02
            reader.feed(dict(updated_at_unix=100.,receiver_running=True,data_source='sdk',streams={'body':dict(fresh=True,age_ms=0,source_timestamp_ns=10**9+i*20000000,payload=p.tolist())}),wall=100.)
        for i in range(16):feed(i)
        self.assertTrue(reader.snapshot()['pose_admission_ready'])
        now[0]+=.26;self.assertFalse(reader.snapshot()['pose_admission_ready'])
        feed(40);self.assertFalse(reader.snapshot()['pose_admission_ready'])
        reader.invalidate('lost');self.assertFalse(reader.snapshot()['pose_admission_ready'])

    def test_transition_freezes_pose_but_keeps_frame_ids_live(self):
        p,_=self.sample();now=[0.];reader=PoseReader('/unused',clock=lambda:now[0])
        def feed(i):
            now[0]=i*.02
            reader.feed(dict(updated_at_unix=100.,receiver_running=True,data_source='sdk',streams={'body':dict(fresh=True,age_ms=0,source_timestamp_ns=10**9+i*20000000,payload=p.tolist())}),wall=100.)
        for i in range(20):feed(i)
        reader.freeze_reference();before=reader.snapshot()
        p[16,3:]=R.from_euler('x',60,degrees=True).as_quat()
        for i in range(20,30):feed(i)
        held=reader.snapshot()
        np.testing.assert_array_equal(held['preview']['joints'],before['preview']['joints'])
        self.assertGreater(held['preview']['frame_index'],before['preview']['frame_index'])
        reader.release_reference();after=reader.snapshot()
        self.assertFalse(np.allclose(held['preview']['joints'],after['preview']['joints']))
        now[0]+=.3;self.assertFalse(reader.snapshot()['ready'])
