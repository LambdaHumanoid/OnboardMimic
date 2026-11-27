import unittest
from unittest.mock import Mock
from tests.runtime import test_native_lifecycle as fixtures

class ExecutorLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.NativeTest();self.f.setUp();self.g=self.f.g
        self.executor=Mock();self.executor.snapshot.return_value=dict(configured=True,fresh=True,armed=True)
        self.g.protection_executor=self.executor
        self.f.command=lambda action:self.g.request('/api/controller',self.f.packet(action=action,confirm_experimental_protection=True,confirm_landing_execution=True))
    def test_prepare_binds_owned_child_before_policy_activation(self):
        self.f.command('prepare_pose')
        self.executor.reset.assert_called_once_with()
        self.executor.arm.assert_called_once_with(self.f.proc.pid)
        self.assertEqual(self.g.phase,'loading')
        self.f.logs='Init Done';self.g.tick();self.assertEqual(self.g.phase,'prepared')
        self.f.command('start_policy');self.f.logs+='\nCONTROL armed\nBumpless policy handoff complete';self.g.tick()
        self.executor.arm.assert_called_once_with(self.f.proc.pid)
    def test_estop_cancels_writer_before_killing_child(self):
        self.f.activate()
        self.executor.cancel.side_effect=lambda:self.assertEqual(self.f.proc.kills,0)
        self.f.command('estop');self.executor.cancel.assert_called_once_with()
        self.assertEqual(self.f.proc.kills,1)
    def test_unconfirmed_cancel_does_not_trigger_roll_by_killing_child(self):
        self.f.activate();self.executor.cancel.side_effect=RuntimeError('unconfirmed stop')
        with self.assertRaisesRegex(RuntimeError,'unconfirmed'):self.f.command('estop')
        self.assertEqual(self.f.proc.kills,0)
    def test_passive_exit_does_not_cancel_independent_executor(self):
        self.f.activate();self.f.proc.code=1;self.g.tick()
        self.executor.cancel.assert_not_called();self.assertEqual(self.g.phase,'error')
    def test_failed_reset_does_not_launch(self):
        self.executor.reset.side_effect=RuntimeError('active protection')
        with self.assertRaises(RuntimeError):self.f.command('prepare_pose')
        self.assertEqual(self.f.launches,0)
    def test_arm_failure_is_visible(self):
        self.executor.arm.side_effect=RuntimeError('bad stream')
        with self.assertRaisesRegex(RuntimeError,'bad stream'):self.f.command('prepare_pose')
        self.assertEqual(self.g.phase,'error');self.assertIn('bad stream',self.g.failure)
    def test_exit_button_requires_confirmed_armed_writer(self):
        self.f.activate();self.g.allow_wbc_exit_tests=True
        self.executor.snapshot.return_value=dict(configured=True,fresh=False,armed=True)
        with self.assertRaisesRegex(ValueError,'尚未确认布防'):self.f.command('test_wbc_sigkill')
        self.assertEqual(self.f.proc.kills,0)
    def test_legacy_start_cannot_silently_arm_landing(self):
        self.f.command('prepare_pose');self.f.logs='Init Done';self.g.tick()
        with self.assertRaisesRegex(ValueError,'刷新控制台'):
            self.g.request('/api/controller',self.f.packet(action='start_policy'))
        self.executor.arm.assert_called_once_with(self.f.proc.pid)
        self.assertEqual(self.g.phase,'prepared')
    def test_legacy_prepare_cannot_silently_enable_protection(self):
        for value in (None,False,'true',1):
            with self.assertRaisesRegex(ValueError,'刷新控制台'):
                self.g.request('/api/controller',self.f.packet(action='prepare_pose',confirm_experimental_protection=value))
        self.executor.arm.assert_not_called();self.assertEqual(self.f.launches,0)
    def test_prepared_process_exit_does_not_cancel_monitor(self):
        self.f.command('prepare_pose');self.f.logs='Init Done';self.g.tick()
        self.f.proc.code=1;self.g.tick()
        self.executor.cancel.assert_not_called();self.assertEqual(self.g.phase,'error')
    def test_legacy_exit_cannot_trigger_a_roll_under_old_no_motion_warning(self):
        self.f.activate();self.g.allow_wbc_exit_tests=True
        for value in (None,False,'true',1):
            with self.assertRaisesRegex(ValueError,'旧版请求'):
                self.g.request('/api/controller',self.f.packet(action='test_wbc_sigterm',confirm_landing_execution=value))
        self.assertEqual(self.f.proc.kills,0)
