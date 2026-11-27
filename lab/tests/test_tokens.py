"""Token admission through the supported LeRobot replay contract."""
import unittest
from onboardmimic_lab.lerobot_replay import validate_plan
from test_native_replay_client import test_plan

class TokensTest(unittest.TestCase):
    def test_bad_dimensions_nonfinite_and_boolean_rejected(self):
        for value in ([0]*63,[0]*76,[float('nan')]*64,[float('inf')]*64,[True]*64):
            plan=test_plan();plan['frames'][0]['sample']['reference']['token_state']=value
            with self.assertRaises(ValueError):validate_plan(plan)
