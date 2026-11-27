import unittest
from onboardmimic.core.protection.probe import run_probe
class ProbeTest(unittest.TestCase):
    def test_isolated_outcomes(self):
        for case,kind in [('process_exit','shadow_request'),('output_timeout','shadow_request'),('state_loss','blocked_state'),('intentional_stop','intentional_stop'),('estop','estop')]:
            result=run_probe(case)
            self.assertTrue(result['test_only']);self.assertFalse(result['physical_effect'])
            self.assertEqual(result['result']['last_event']['kind'],kind)
    def test_unrecognized_action_rejected(self):
        with self.assertRaises(ValueError):run_probe('kill_real_wbc')
