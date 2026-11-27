import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from onboardmimic_lab.capture_incidents import CaptureIncidents
from onboardmimic_lab.reference_dataset import RawEpisode
from test_reference import spec, sample


class CaptureIncidentTests(unittest.TestCase):
    def test_gap_saved_before_finish_and_survives_raw_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);s=spec();raw=RawEpisode(root/'pending'/'episode',dict(controller_spec=s))
            sink=CaptureIncidents(root/'diagnostics'/'episode',dict(controller_spec=s))
            raw.incidents=sink
            try:
                for i in (0,1,3,4,6):
                    x=sample(s,i);raw.controller(x,x['timestamp_monotonic_ns'],take_ownership=True)
                sink.close()
                status=sink.snapshot()
                self.assertEqual(status['count'],2)
                self.assertTrue(all(x['state']=='saved' for x in status['recent']))
                files=list((root/'diagnostics').glob('*/*/evidence.json'))
                self.assertEqual(len(files),2)
                records=[json.loads(p.read_text()) for p in files]
                self.assertEqual({(r['previous_sample']['index'],r['sample']['index']) for r in records},{(1,3),(4,6)})
                self.assertEqual(records[0]['episode']['controller_spec'],s)
                for record in records:
                    self.assertEqual(record['previous_sample'],sample(s,record['previous_sample']['index']))
                    self.assertEqual(record['sample'],sample(s,record['sample']['index']))
                raw.finish();shutil.rmtree(root/'pending')
                self.assertTrue(all(p.exists() for p in files))
            finally:
                sink.close()
                if not all(x.closed for x in raw.streams.values()):raw.finish()

    def test_default_callers_keep_mutation_isolation_and_full_saved_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            s=spec();raw=RawEpisode(Path(d)/'episode',dict(controller_spec=s))
            row=sample(s,0)
            row['capture_diagnostics']={'publication_sequence':1,'nested':{'values':[1,2,3]}}
            raw.controller(row,row['timestamp_monotonic_ns'])
            row['capture_diagnostics']['nested']['values'][0]=999
            self.assertEqual(raw.previous['capture_diagnostics']['nested']['values'],[1,2,3])
            raw.finish()
            saved=json.loads((raw.root/'controller.jsonl').read_text())['sample']
            self.assertEqual(saved['capture_diagnostics'],raw.previous['capture_diagnostics'])
            event=json.loads((raw.root/'runtime.jsonl').read_text())
            self.assertEqual(event['sample_ref'],dict(file='controller.jsonl',session_id=saved['session_id'],index=0))
            self.assertNotIn('publication',event)

    def test_disk_failure_reported_without_failing_capture(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'file';root.write_text('not a directory')
            sink=CaptureIncidents(root,{})
            sink.submit(sample(spec(),1),sample(spec(),3),123)
            sink.close()
            status=sink.snapshot()
            self.assertEqual(status['recent'][0]['state'],'failed')
            self.assertTrue(status['recent'][0]['error'])

    def test_slow_storage_does_not_block_producer_and_queue_full_is_visible(self):
        gate=threading.Event()
        with patch.object(CaptureIncidents,'_run',lambda self:gate.wait(2)):
            sink=CaptureIncidents('/unused',{})
            try:
                a,b=sample(spec(),1),sample(spec(),3)
                start=time.monotonic()
                for i in range(20):sink.submit(a,b,123)
                self.assertLess(time.monotonic()-start,.3)
                self.assertEqual(sink.snapshot()['count'],20)
                self.assertEqual(sink.snapshot()['recent'][-1]['error'],'diagnostic_queue_full')
            finally:gate.set();sink.close()

    def test_collector_reports_complete_stream_loss_and_saves_last_sample(self):
        from onboardmimic_lab.collection import Collector
        from test_collection import fixture_config,active
        with tempfile.TemporaryDirectory() as d:
            def worker(raw):
                row=sample(spec(),1)
                raw.controller(row,row['timestamp_monotonic_ns'])
                raise ValueError('Stream lost freshness: controller')
            owner=Collector(fixture_config(Path(d)),active,worker=worker)
            try:
                owner.command(dict(action='start',task='diagnostic test'))
                owner.thread.join(3)
                result=owner.status()
                self.assertEqual(result['state'],'failed')
                self.assertEqual(result['discontinuities']['count'],1)
                self.assertEqual(result['discontinuities']['saved'],1)
                event=result['discontinuities']['recent'][0]
                self.assertEqual(event['kind'],'capture_error')
                self.assertIn('Stream lost freshness',event['reason'])
                evidence=json.loads((Path(event['path'])/'evidence.json').read_text())
                self.assertEqual(evidence['previous_sample']['index'],1)
                self.assertIsNone(evidence['sample'])
            finally:owner.close()
