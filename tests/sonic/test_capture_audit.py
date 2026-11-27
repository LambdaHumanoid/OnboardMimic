import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from onboardmimic.backends.sonic.native.audit import Audit
from tests.sonic import test_native_consumption as fixtures


class AuditTests(unittest.TestCase):
    @unittest.skipUnless(Path('/proc/self/task').exists(),'Linux scheduling diagnostics')
    def test_receiver_scheduler_sample_is_timestamped_on_audit_thread(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'receiver.jsonl';audit=Audit(path)
            audit.emit('receiver_loop',thread_id=threading.get_native_id(),max_active_batch_ns=0)
            audit.close()
            row=json.loads(path.read_text())
            scheduler=row['scheduler']
            self.assertNotIn('error',scheduler)
            self.assertGreaterEqual(scheduler['sampled_ns'],row['at_ns'])
            self.assertGreaterEqual(scheduler['runqueue_ns'],0)
            self.assertGreater(scheduler['runtime_ns'],0)
            self.assertFalse(audit.error)

    def test_rotation_and_gap_snapshot_are_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'consumption-receiver-test.jsonl';a=Audit(p,max_bytes=512)
            for i in range(40):a.emit('received',index=i)
            a.emit('join_gap',previous_index=38,index=40)
            a.close()
            self.assertFalse(a.error)
            folders=list((Path(d)/'capture-incidents').iterdir())
            self.assertEqual(len(folders),1)
            self.assertTrue((folders[0]/'snapshot.json').exists())
            self.assertEqual(a.latest_incident['state'],'saved')
            self.assertLessEqual(p.stat().st_size,512)
            self.assertLessEqual(Path(str(p)+'.1').stat().st_size,512)
            self.assertTrue(any(json.loads(x)['stage']=='join_gap' for x in p.read_text().splitlines()))

    def test_later_superseded_attempt_does_not_overwrite_gap_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'consumption-receiver-test.jsonl';a=Audit(p)
            a.emit('join_gap',diagnostic_id='first',index=3,consumption={'reason':'unresolved'})
            a.emit('join_gap',index=5,consumption={'reason':'superseded_before_commit'})
            a.emit('join_gap',diagnostic_id='second',index=8,consumption={'reason':'unresolved'})
            a.close()
            root=Path(d)/'capture-incidents'
            self.assertEqual({p.name for p in root.iterdir()},{'first','second'})
            self.assertEqual(json.loads((root/'first/snapshot.json').read_text())['event']['index'],3)
            self.assertEqual(a.latest_incident['id'],'second')

    def test_blocked_writer_does_not_block_producer(self):
        gate=threading.Event()
        with patch.object(Audit,'_run',lambda self:gate.wait(2)):
            a=Audit('/unused',capacity=2);start=time.monotonic()
            for i in range(1000):a.emit('received',index=i)
            self.assertEqual(a.dropped,998)
            self.assertLess(time.monotonic()-start,.5)
            gate.set();a.close()

    def test_disk_failure_is_visible_not_raised_to_producer(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'file';p.write_text('x');a=Audit(p/'impossible')
            a.thread.join(1);a.emit('received',index=1)
            self.assertTrue(a.error);a.close()

    def test_join_records_specific_rejection_and_late_evidence(self):
        case=fixtures.NativeConsumptionTests();case.setUp();events=[]
        case.join.trace=lambda stage,**fields:events.append(dict(stage=stage,**fields))
        case.commit['command']['q'][0]+=1
        self.assertIsNone(case.consume())
        self.assertTrue(any(x.get('reason')=='protected_command_differs' for x in events))
        case.setUp();case.join.trace=lambda stage,**fields:events.append(dict(stage=stage,**fields))
        case.consume();case.emit('native',case.mapping)
        self.assertEqual(events[-1]['reason'],'already_joined_or_superseded')

    def test_archive_copy_failure_does_not_disable_audit_or_claim_saved(self):
        with tempfile.TemporaryDirectory() as d:
            a=Audit(Path(d)/'consumption-receiver-test.jsonl')
            with patch('onboardmimic.backends.sonic.native.audit.shutil.copyfile',side_effect=OSError('disk full')):
                a.emit('join_gap',diagnostic_id='failure',consumption={'reason':'unresolved'})
                a.close()
            self.assertEqual(a.latest_incident['state'],'failed')
            self.assertTrue(a.latest_incident['errors'])
            self.assertFalse(a.error)
