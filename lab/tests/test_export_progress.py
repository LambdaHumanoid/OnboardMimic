import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from onboardmimic_lab.export_progress import ExportProgress, read_progress
from onboardmimic_lab.export_jobs import ExportJobs


class ProgressTest(unittest.TestCase):
    def test_throttle_stage_transition_and_completion_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            p=ExportProgress(folder)
            with patch('onboardmimic_lab.export_progress.time.monotonic',side_effect=[1,1.1,1.6,1.7,1.8]):
                p.update('video',10,100)
                self.assertEqual(read_progress(folder)['percent'],10)
                p.update('video',20,100)
                self.assertEqual(read_progress(folder)['percent'],10)
                p.update('video',100,100)
                self.assertEqual(read_progress(folder)['percent'],99)
                p.update('verify_output',0,100)
                self.assertEqual(read_progress(folder)['percent'],0)
                p.update('saving')
                self.assertNotIn('percent',read_progress(folder))

    def test_status_preserves_pause_and_ignores_stale_retry_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);episode=root/'episodes'/'abc';episode.mkdir(parents=True)
            ExportProgress(episode).update('video',30,100)
            jobs=ExportJobs.__new__(ExportJobs);jobs.root=root;jobs.lock=threading.RLock()
            jobs.jobs={'abc':dict(id='abc',state='paused')}
            result=jobs.status()[0]
            self.assertEqual((result['state'],result['progress']['percent']),('paused',30))
            jobs.jobs['abc']['state']='queued'
            self.assertNotIn('progress',jobs.status()[0])
            self.assertNotIn('progress',jobs.jobs['abc'])
            (episode/'processing-progress.json').write_text('{')
            self.assertIsNone(read_progress(episode))
