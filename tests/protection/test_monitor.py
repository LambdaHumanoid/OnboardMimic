import unittest
from onboardmimic.core.protection import ProtectionMonitor, OutputEvidence

class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.now=10.;self.events=[]
        self.m=ProtectionMonitor(lambda:self.now,event_sink=self.events.append)
        self.m.begin('run-1','replaceable-wbc');self.m.activate()
    def test_crash_latched_once_and_restart_does_not_rearm(self):
        self.m.observe(False,True);self.m.observe(True,True);self.m.observe(False,True)
        self.assertEqual(len(self.events),1)
        self.assertEqual(self.m.snapshot()['state'],'latched')
        self.assertFalse(self.m.output(OutputEvidence('run-1',5,10.)))
    def test_only_new_commands_renew_deadline(self):
        self.assertTrue(self.m.output(OutputEvidence('run-1',1,10.)))
        self.now=10.05
        self.assertFalse(self.m.output(OutputEvidence('run-1',1,self.now)))
        self.assertFalse(self.m.output(OutputEvidence('old',2,self.now)))
        self.now=10.11;self.m.observe(True,True)
        self.assertEqual(self.events[0]['reason'],'wbc_output_timeout')
    def test_no_fake_heartbeat_from_process_liveness(self):
        self.now=100.;self.m.observe(True,True)
        self.assertEqual(self.events,[])
        self.assertEqual(self.m.snapshot()['output_monitoring'],'unavailable')
    def test_intentional_stop_before_process_exit(self):
        self.m.stop();self.m.observe(False,True)
        self.assertEqual(self.events,[])
    def test_estop_overrides_latched_fault(self):
        self.m.observe(False,True);self.m.stop(estop=True);self.m.stop()
        self.assertEqual(self.m.snapshot()['state'],'estop')
    def test_no_state_blocks_candidate(self):
        self.m.observe(False,False)
        self.assertEqual(self.events[0]['kind'],'blocked_state')
        self.assertFalse(self.events[0]['actuation'])
    def test_new_explicit_session_rejects_old_output(self):
        self.m.observe(False,True);self.m.begin('run-2','other-wbc');self.m.activate()
        self.assertFalse(self.m.output(OutputEvidence('run-1',9,10.)))
        self.assertTrue(self.m.output(OutputEvidence('run-2',1,10.)))
    def test_invalid_evidence(self):
        for seq,at in [(True,10.),(-1,10.),(1,11.),(1,float('nan')),(1,9.)]:
            self.assertFalse(self.m.output(OutputEvidence('run-1',seq,at)))
    def test_bad_sink_does_not_block_fault(self):
        def sink(_):raise RuntimeError('disk full')
        self.m.event_sink=sink
        self.assertEqual(self.m.observe(False,True)['kind'],'shadow_request')
