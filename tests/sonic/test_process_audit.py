"""Audit process lifecycle and failure behavior; local files/loopback only."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from onboardmimic.backends.sonic.native.process_audit import ProcessAudit


def stalled_writer(*args): time.sleep(10)


class ProcessAuditTests(unittest.TestCase):
    def test_fifo_archive_rotation_status_and_scheduler_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'consumption-test.jsonl';audit=ProcessAudit(path,max_bytes=2048)
            pid=audit.process.pid
            for i in range(30):audit.emit('received',index=i,audit_drops=99)
            audit.emit('receiver_loop',thread_id=threading.get_native_id(),max_active_batch_ns=0)
            audit.emit('join_gap',diagnostic_id='offline-gap',index=32,consumption={'reason':'unresolved'})
            audit.close();audit.close()
            self.assertNotEqual(pid,os.getpid())
            self.assertFalse(audit.process.is_alive());self.assertFalse(audit.error)
            self.assertEqual(audit.latest_incident['state'],'saved')
            self.assertEqual(audit.latest_incident['id'],'offline-gap')
            files=list(Path(directory).glob('consumption-test.jsonl*'))
            rows=[json.loads(l) for f in files for l in f.read_text().splitlines()]
            scheduler=next(r['scheduler'] for r in rows if r['stage']=='receiver_loop')
            self.assertNotIn('error',scheduler)
            self.assertGreater(scheduler['receiver_rss_kb'],0)
            self.assertGreater(scheduler['runtime_ns'],0)
            self.assertTrue(all(r['audit_drops']==0 for r in rows))
            snap=json.loads((Path(directory)/'capture-incidents/offline-gap/snapshot.json').read_text())
            self.assertEqual(snap['event']['index'],32)

    def test_partial_batch_flush_preserves_fifo_timestamps_and_no_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'rows.jsonl';audit=ProcessAudit(path)
            for i in range(35):audit.emit('received',index=i)
            # Explicit check also flushes a partial batch when traffic stops.
            audit.check(force=True)
            audit.close()
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([r['index'] for r in rows],list(range(35)))
            self.assertEqual([r['at_ns'] for r in rows],sorted(r['at_ns'] for r in rows))
            self.assertEqual(audit.dropped,0)
            self.assertFalse(audit.error)

    def test_authority_fault_flushes_prior_records_without_waiting_for_timer(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'rows.jsonl';audit=ProcessAudit(path)
            try:
                audit.emit('received',index=1)
                audit.emit('rejected',reason='authority_lost')
                deadline=time.monotonic()+2
                # No check()/close() flush: the fault itself must submit the tail.
                while time.monotonic()<deadline:
                    if path.exists() and 'authority_lost' in path.read_text():break
                    time.sleep(.01)
                rows=[json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual([r['stage'] for r in rows],['received','rejected'])
            finally:audit.close()

    def test_stalled_writer_cannot_block_producer_or_grow_unbounded(self):
        with patch('onboardmimic.backends.sonic.native.process_audit._write',stalled_writer):
            audit=ProcessAudit('/unused',capacity=2)
        try:
            began=time.monotonic()
            for i in range(1000):audit.emit('received',index=i)
            self.assertLess(time.monotonic()-began,.5)
            self.assertEqual(audit.dropped,998)
        finally:audit.close()
        self.assertFalse(audit.process.is_alive())
        self.assertIn('timeout',audit.error)

    def test_disk_failure_is_reported_without_raising_into_receiver(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'not-directory';file.write_text('x')
            audit=ProcessAudit(file/'journal.jsonl')
            try:
                deadline=time.monotonic()+3
                while not audit.error and time.monotonic()<deadline:
                    audit.check(force=True);time.sleep(.01)
                self.assertIn('FileExistsError',audit.error)
                audit.emit('received',index=1)
            finally:audit.close()

    def test_worker_death_is_explicit_and_close_reaps_it(self):
        with tempfile.TemporaryDirectory() as directory:
            audit=ProcessAudit(Path(directory)/'journal.jsonl')
            try:
                audit.process.kill();audit.process.join(1);audit.check(force=True)
                self.assertIn('exited',audit.error)
                audit.emit('received',index=1)
            finally:audit.close()
            self.assertFalse(audit.process.is_alive())
