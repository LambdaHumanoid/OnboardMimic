import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
from onboardmimic.core.runtime_timing import wait_event, join_thread


class TimingTest(unittest.TestCase):
    def test_cancel_and_timeout_are_bounded(self):
        event=threading.Event()
        begin=time.monotonic();self.assertFalse(wait_event(event,.02))
        self.assertGreaterEqual(time.monotonic()-begin,.018)
        def set_later():time.sleep(.03);event.set()
        worker=threading.Thread(target=set_later);worker.start()
        begin=time.monotonic();self.assertTrue(wait_event(event,3))
        self.assertLess(time.monotonic()-begin,.3)
        self.assertTrue(join_thread(worker,1))

    @unittest.skipUnless(sys.platform=='linux' and shutil.which('cc'), 'Linux C compiler needed for fault injection')
    def test_transport_survives_realtime_semaphore_fault(self):
        with tempfile.TemporaryDirectory() as tmp:
            library=Path(tmp)/'realtime_wait_probe.so'
            subprocess.run(['cc','-shared','-fPIC','-O2',str(ROOT/'tests/realtime_wait_probe.c'),'-o',str(library),'-ldl','-pthread'],check=True)
            env=dict(os.environ,LD_PRELOAD=str(library))
            probe='tests.runtime_clock_probe'
            if sys.version_info[:2]==(3,8):
                baseline=subprocess.run([sys.executable,'-m',probe,'--event-baseline'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(baseline.returncode,0,baseline.stderr)
                self.assertIn('Injected realtime semaphore delay',baseline.stderr)
                self.assertGreater(json.loads(baseline.stdout)['event_wait_seconds'],1.8)
            result=subprocess.run([sys.executable,'-m',probe],cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            value=json.loads(result.stdout.strip())
            self.assertFalse(value['watchdog_shutdown'],result.stderr)
            self.assertFalse(value['controller_created'])
            self.assertIsNone(value['transport_error'])
            self.assertNotIn('Injected realtime semaphore delay',result.stderr)


if __name__=='__main__':unittest.main()
