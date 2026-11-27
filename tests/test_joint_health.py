import unittest
from onboardmimic.robots.unitree_g1.telemetry import joint_health
class JointHealthTest(unittest.TestCase):
 def test_max_temperature_fault_and_missing(self):
  h=joint_health({'rt/lowstate':(1,{'motor_state':[{'temperature':[45,72],'motorstate':4},{'temperature':[]}]})},1.1)
  self.assertTrue(h['fresh']);self.assertEqual(len(h['joints']),29)
  self.assertEqual(h['joints'][0]['temperature_c'],72);self.assertIsNone(h['joints'][0]['hot']);self.assertEqual(h['joints'][0]['fault'],4)
  self.assertIsNone(h['joints'][1]['temperature_c'])
 def test_stale_does_not_keep_hot_values(self):
  h=joint_health({'rt/lowstate':(1,{'motor_state':[{'temperature':[80]}]})},2)
  self.assertFalse(h['fresh']);self.assertIsNone(h['joints'][0]['temperature_c'])
 def test_invalid_temperatures(self):
  h=joint_health({'rt/lowstate':(1,{'motor_state':[{'temperature':[float('nan'),True,None]}]})},1)
  self.assertIsNone(h['joints'][0]['temperature_c'])

 def test_missing_sensor_does_not_shift_channels(self):
  h=joint_health({'rt/lowstate':(1,{'motor_state':[{'temperature':[None,90]}]})},1)
  self.assertEqual(h['joints'][0]['temperatures_c'],[None,90])
  self.assertIsNone(h['warning_c']);self.assertIsNone(h['joints'][0]['hot'])
