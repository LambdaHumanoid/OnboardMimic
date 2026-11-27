import unittest
from detector import Detector, Observation

class Checks(unittest.TestCase):
    def detector(self):
        d=Detector();d.start_session('run-1',True);return d
    def test_not_armed_at_startup(self):
        self.assertEqual(Detector().observe(Observation(1,0,1,False)),'DISARMED')
    def test_normal(self):
        self.assertEqual(self.detector().observe(Observation(1,.99,1,True)),'MONITORING')
    def test_crash_and_hang(self):
        for alive in (False,True):
            d=self.detector()
            self.assertEqual(d.observe(Observation(1,.9,1,alive)),'SHADOW_ROLL_REQUEST')
            self.assertEqual(d.observe(Observation(1.01,1.01,1.01,True)),'LATCHED')
    def test_intent_before_stop(self):
        d=self.detector();d.intentional_stop('run-1')
        self.assertEqual(d.observe(Observation(1,0,1,False)),'DISARMED')
    def test_state_loss(self):
        self.assertEqual(self.detector().observe(Observation(1,0,0,False)),'BLOCKED_STATE_LOST')
    def test_estop_overrides_everything(self):
        d=self.detector();d.intentional_stop('run-1')
        self.assertEqual(d.observe(Observation(1,0,0,False,estop=True)),'ESTOP_PRIORITY')
    def test_ownership(self):
        self.assertEqual(self.detector().observe(Observation(1,0,1,False,ownership_available=False)),'BLOCKED_CONTROL_UNAVAILABLE')
    def test_old_stop_cannot_cancel_new_session(self):
        d=self.detector();d.start_session('run-2',True)
        with self.assertRaises(ValueError):d.intentional_stop('run-1')
    def test_bad_clock(self):
        d=self.detector()
        with self.assertRaises(ValueError):d.observe(Observation(1,2,1,True))

if __name__=='__main__':unittest.main()
