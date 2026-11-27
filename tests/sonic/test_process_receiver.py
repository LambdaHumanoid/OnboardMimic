"""Real spawned process and loopback telemetry, no robot interfaces."""
import os
import time
import unittest
from unittest.mock import patch
import tests.sonic.test_native_consumption as native_tests
from onboardmimic.backends.sonic.native.process_receiver import ProcessReceiver


class IsolatedReceiverTests(native_tests.NativeConsumptionTests):
    # Exercise the existing full inference/commit/replay publication test using a
    # real spawned worker, keeping the same consumed evidence and manifest assertions.
    def test_real_receiver_two_frame_output_and_manifest_export(self):
        created=[]
        def factory(*args, **kwargs):
            r=ProcessReceiver(*args, **kwargs);created.append(r);return r
        with patch('tests.sonic.test_native_consumption.Receiver', factory):
            super().test_real_receiver_two_frame_output_and_manifest_export()
        r=created[0]
        self.assertNotIn('decoder_input',r.join.last)
        self.assertNotIn('state_history_ns',r.join.last)
        self.assertFalse(r.process.is_alive())

    def test_heartbeat_never_refreshes_inference_freshness(self):
        # Parent applies the original source timestamp even when the child is alive.
        from unittest.mock import Mock
        r=ProcessReceiver(17689,17690,17691)
        r.process=Mock();r.process.is_alive.return_value=True
        self.consume();r.join=self.join
        self.assertTrue(r.status()['native_consumption_fresh'])
        self.now+=61000000
        self.assertFalse(r.status()['native_consumption_fresh'])

    def test_publication_bind_failure_reaches_runtime(self):
        import socket
        holders=[];ports=[]
        for _ in range(3):
            sock=socket.socket();sock.bind(('127.0.0.1',0));holders.append(sock);ports.append(sock.getsockname()[1])
        holders[2].listen(1)
        r=ProcessReceiver(*ports);r.thread.start()
        try:
            deadline=time.monotonic()+6
            while r.error is None and time.monotonic()<deadline:time.sleep(.01)
            self.assertIsNotNone(r.error)
            self.assertIn('Address already in use',r.error)
            self.assertFalse(r.status()['record_ready'])
            self.assertIn('Address already in use',r.status()['diagnostics']['receiver_error'])
        finally:
            r.close()
            for sock in holders:sock.close()
        self.assertFalse(r.process.is_alive())

    def test_worker_death_revokes_even_cached_evidence(self):
        import socket
        ports=[]
        for _ in range(3):
            with socket.socket() as s:
                s.bind(('127.0.0.1',0));ports.append(s.getsockname()[1])
        r=ProcessReceiver(*ports);r.thread.start()
        try:
            deadline=time.monotonic()+5
            while (r.process is None or r.process.pid is None) and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertIsNotNone(r.process.pid)
            self.assertNotEqual(r.process.pid,os.getpid())
            r.process.kill();r.process.join(timeout=2)
            self.assertFalse(r.status()['record_ready'])
            deadline=time.monotonic()+2
            while r.error is None and time.monotonic()<deadline:time.sleep(.01)
            self.assertIsNotNone(r.error)
        finally:r.close()
        self.assertFalse(r.process.is_alive())
