"""No ROS or robot: connection ordering, release failures, and incumbent protection."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from onboardmimic.robots.unitree_g1.connect_services import connect, owner_lock
from onboardmimic.robots.unitree_g1.bridge.motion_mode import MotionMode, RpcTimeout, release_mode, validate_motors


class ConnectionTest(unittest.TestCase):
    def ops(self):
        ops = Mock()
        ops.bridge_state.return_value = 'failed'
        ops.lock.return_value = self.fake_lock()
        return ops

    @contextmanager
    def fake_lock(self):
        yield

    def test_release_precedes_start_and_ready_is_last(self):
        ops = self.ops()
        connect(ops)
        names = [call[0] for call in ops.mock_calls]
        self.assertLess(names.index('feedback'), names.index('release'))
        self.assertLess(names.index('release'), names.index('start_bridge'))
        self.assertLess(names.index('start_bridge'), names.index('wait_ready'))
        self.assertEqual(ops.report.call_args.args[0], 'ready')

    def test_pre_release_failure_never_starts_bridge(self):
        for method in ('conflicts','configure','feedback','release'):
            with self.subTest(method=method):
                ops = self.ops()
                getattr(ops, method).side_effect = RuntimeError(method)
                with self.assertRaises(RuntimeError): connect(ops)
                ops.start_bridge.assert_not_called()
                if method != 'release': ops.release.assert_not_called()

    def test_failed_bridge_never_reports_ready(self):
        ops = self.ops()
        ops.wait_ready.side_effect = RuntimeError('foreign writer')
        with self.assertRaises(RuntimeError): connect(ops)
        self.assertNotEqual(ops.report.call_args.args[0], 'ready')

    def test_reuse_idle_owner_without_mode_release_or_restart(self):
        ops = self.ops()
        ops.bridge_state.return_value = 'active'
        connect(ops)
        ops.ready.assert_called_once_with()
        ops.release.assert_not_called()
        ops.start_bridge.assert_not_called()
        # An active policy or another bridge makes the physical guard fail.
        ops.ready.side_effect = RuntimeError('controller running')
        with self.assertRaises(RuntimeError): connect(ops)
        ops.release.assert_not_called()

    def test_lock_preserves_incumbent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp)/'owner')
            with owner_lock(path):
                with self.assertRaisesRegex(RuntimeError, 'incumbent unchanged'):
                    with owner_lock(path): self.fail('entered')
            with owner_lock(path): pass
            self.assertTrue(Path(path).exists())

    def test_no_release_if_already_released(self):
        client = Mock(spec=MotionMode)
        client.check_mode.return_value = {'name':''}
        self.assertFalse(release_mode(client)['released'])
        client.release.assert_not_called()

    def test_release_confirmed_before_success(self):
        client = Mock(spec=MotionMode)
        client.check_mode.side_effect = [{'name':'normal'},{'name':'normal'},{'name':''}]
        self.assertTrue(release_mode(client, sleep=lambda _:None)['released'])
        client.check_feedback.assert_called_once_with()
        client.release.assert_called_once_with()

    def test_unconfirmed_release_is_not_retried(self):
        client = Mock(spec=MotionMode)
        client.check_mode.return_value = {'name':'normal'}
        client.release.side_effect = RuntimeError('RPC timeout')
        with self.assertRaisesRegex(RuntimeError,'RPC timeout'): release_mode(client)
        client.release.assert_called_once_with()
        client.release.side_effect = None
        client.release.reset_mock()
        with self.assertRaisesRegex(RuntimeError, 'not confirmed'): release_mode(client, timeout=0)
        client.release.assert_called_once_with()

    def test_bad_feedback_prevents_release(self):
        client = Mock(spec=MotionMode)
        client.check_mode.return_value = {'name':'normal'}
        client.check_feedback.side_effect = RuntimeError('bad feedback')
        with self.assertRaises(RuntimeError): release_mode(client)
        client.release.assert_not_called()

    def test_only_read_only_rpc_retries_lost_response(self):
        client = object.__new__(MotionMode)
        client.call = Mock(side_effect=[RpcTimeout('lost'), '{"name":"ai"}'])
        self.assertEqual(client.check_mode()['name'], 'ai')
        self.assertEqual([call.args[0] for call in client.call.call_args_list], [1001,1001])
        client.call = Mock(side_effect=RpcTimeout('lost release response'))
        with self.assertRaises(RpcTimeout): client.release()
        client.call.assert_called_once_with(1003)

    def test_read_retry_is_bounded_and_does_not_retry_vendor_rejection(self):
        client = object.__new__(MotionMode)
        client.call = Mock(side_effect=RpcTimeout('lost'))
        with self.assertRaises(RpcTimeout): client.check_mode()
        self.assertEqual(client.call.call_count, 3)
        client.call = Mock(side_effect=RuntimeError('vendor rejection'))
        with self.assertRaises(RuntimeError): client.check_mode()
        client.call.assert_called_once()

    def test_fault_feedback_validation_allows_disabled_motors(self):
        motors = [SimpleNamespace(q=0.,dq=0.,motorstate=0,mode=0) for _ in range(29)]
        validate_motors(motors)
        for field, value in [('motorstate',1),('q',float('nan')),('dq',13.)]:
            with self.subTest(field=field):
                previous = getattr(motors[0],field)
                setattr(motors[0],field,value)
                with self.assertRaises(RuntimeError): validate_motors(motors)
                setattr(motors[0],field,previous)


if __name__ == '__main__': unittest.main()
