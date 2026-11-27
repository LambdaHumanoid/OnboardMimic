import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from onboardmimic.backends.sonic.native.incident import save_incident, _lock


class IncidentTests(unittest.TestCase):
    def test_copy_runs_off_caller_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'consumption-native-1.jsonl').write_text('evidence')
            entered=threading.Event();release=threading.Event()
            import shutil
            copy=shutil.copyfile
            def slow_copy(a,b):
                entered.set();release.wait(3);return copy(a,b)
            with patch('onboardmimic.backends.sonic.native.incident.shutil.copyfile',slow_copy):
                name=save_incident(dict(run=12,reason='consumption_timeout'),root)
                self.assertTrue(entered.wait(2))
                self.assertIsNone(save_incident(dict(run=13),root))
                release.set()
                self.assertTrue(_lock.acquire(timeout=3));_lock.release()
            folder=root/'replay-incidents'/name
            self.assertEqual(json.loads((folder/'snapshot.json').read_text())['reason'],'consumption_timeout')
            self.assertEqual((folder/'consumption-native-1.jsonl').read_text(),'evidence')
            for run in range(4):
                save_incident(dict(run=run),root)
                self.assertTrue(_lock.acquire(timeout=3));_lock.release()
            self.assertEqual(len(list((root/'replay-incidents').iterdir())),3)
