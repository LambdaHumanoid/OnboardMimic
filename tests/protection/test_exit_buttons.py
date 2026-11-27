import signal
import unittest
from tests.runtime import test_native_lifecycle as fixtures

class ExitButtonTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.NativeTest();self.f.setUp();self.f.activate();self.g=self.f.g
        self.sent=[]
        def send(sig):self.sent.append(sig);self.f.proc.code=-int(sig)
        self.f.proc.send_signal=send
    def test_disabled_by_default(self):
        with self.assertRaises(ValueError):self.f.command('test_wbc_sigkill')
        self.assertEqual(self.sent,[])
    def test_owned_exit_is_observed_as_fault(self):
        self.g.allow_wbc_exit_tests=True;self.f.command('test_wbc_sigterm')
        self.assertEqual(self.sent,[signal.SIGTERM])
        self.assertEqual(self.g.protection.event['reason'],'wbc_process_exit')
        self.assertFalse(self.g.status()['protection']['automatic_execution'])
    def test_kill_and_no_retry(self):
        self.g.allow_wbc_exit_tests=True;self.f.command('test_wbc_sigkill')
        self.assertEqual(self.sent,[signal.SIGKILL])
        with self.assertRaises(ValueError):self.f.command('test_wbc_sigkill')
        self.assertEqual(len(self.sent),1)
    def test_estop_still_takes_priority(self):
        self.g.allow_wbc_exit_tests=True;self.f.command('estop')
        with self.assertRaises(ValueError):self.f.command('test_wbc_sigkill')
        self.assertEqual(self.sent,[])
