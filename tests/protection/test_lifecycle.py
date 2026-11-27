import unittest
from tests.runtime import test_native_lifecycle as fixtures
from onboardmimic.core.protection import OutputEvidence

class LifecycleProtectionTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.NativeTest();self.fixture.setUp();self.fixture.activate()
        self.g=self.fixture.g
    def test_crash_records_before_existing_stop(self):
        self.fixture.proc.code=1;self.g.tick()
        self.assertEqual(self.g.phase,'error')
        p=self.g.status()['protection']
        self.assertEqual(p['last_event']['reason'],'wbc_process_exit')
        self.assertFalse(p['automatic_execution'])
        self.g.tick();self.assertEqual(self.g.protection.event_count,1)
    def test_stop_movement_is_not_wbc_shutdown(self):
        self.g.control_request(self.fixture.packet(stop=True),self.fixture.now)
        self.assertEqual(self.g.protection.state,'monitoring')
    def test_estop_cannot_generate_roll_request(self):
        self.fixture.command('estop')
        self.assertEqual(self.g.protection.state,'estop')
        self.assertEqual(self.g.protection.event_count,0)
        self.assertEqual(self.fixture.proc.kills,1)
    def test_output_adapter_is_generic_and_shadow_does_not_halt_wbc(self):
        self.g.backend.protection_output=lambda topic,value,session,at:OutputEvidence(session,value['seq'],at) if topic=='test_wbc/accepted' else None
        self.g.ingest('test_wbc/accepted',{'seq':1})
        self.fixture.now+=.11;self.fixture.refresh();self.g.tick()
        self.assertEqual(self.g.protection.event['reason'],'wbc_output_timeout')
        self.assertTrue(self.g.alive())
    def test_bad_optional_adapter_does_not_break_ingest(self):
        self.g.backend.protection_output=lambda *args:object()
        self.fixture.refresh()
        self.assertTrue(self.g.alive())
        self.assertTrue(self.g.status()['protection']['adapter_error'])
    def test_invalid_feedback_blocks_candidate(self):
        self.g.latest.clear();self.g.tick()
        self.assertEqual(self.g.protection.event['kind'],'blocked_state')
    def test_monitor_failure_never_blocks_estop(self):
        def broken(*args,**kwargs):raise RuntimeError('monitor error')
        self.g.protection.stop=broken
        self.fixture.command('estop')
        self.assertEqual(self.fixture.proc.kills,1)
        self.assertEqual(self.g.phase,'stopped')
