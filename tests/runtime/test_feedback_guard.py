import math
from pathlib import Path
import sys
import unittest

from onboardmimic.robots.unitree_g1.continuity.health import FeedbackHealth
from tests.fixtures import FeedbackLifecycle
from tests.runtime.test_native_lifecycle import Process


class HealthTests(unittest.TestCase):
    def row(self, **kw):
        return dict(protocol=1, session='first', sequence=1, generation=1,
                    at=1., source='primary', **kw)

    def test_transition_requires_new_dual_feedback_and_settle(self):
        h = FeedbackHealth(); self.assertTrue(h.ingest(self.row(), 1.))
        data = {t: (1.04, {}) for t in ('rt/lowstate', 'rt/secondary_imu')}
        self.assertFalse(h.ready(1.03, data))
        self.assertTrue(h.ready(1.041, data))
        self.assertFalse(h.ready(1.10, data))
        changed = self.row(); changed.update(source='standby', sequence=2, generation=2, at=1.1)
        self.assertTrue(h.ingest(changed, 1.1))
        self.assertFalse(h.ready(1.15, data))
        data = {t: (1.15, {}) for t in data}
        self.assertTrue(h.ready(1.15, data))

    def test_replayed_stale_future_and_retired_sessions_rejected(self):
        h = FeedbackHealth(); h.ingest(self.row(), 1.)
        self.assertFalse(h.ingest(self.row(), 1.01))
        for at in (float('nan'), float('inf'), 2., .5):
            row=self.row(); row.update(at=at, sequence=2)
            self.assertFalse(h.ingest(row, 1.01))
        row=self.row(); row.update(session='second',at=1.01)
        self.assertTrue(h.ingest(row, 1.01))
        row=self.row(); row.update(sequence=20,at=1.02)
        self.assertFalse(h.ingest(row, 1.02))
        self.assertEqual(h.status['session'], 'second')


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now=100.;self.seq=0;self.source_seq=0;self.proc=Process();self.log=''
        self.launches=0
        def launch():self.launches+=1;return self.proc
        self.g=FeedbackLifecycle([0.]*29,launch,lambda:self.log,lambda:None,clock=lambda:self.now)

    def feedback(self, source='primary', generation=1):
        self.source_seq+=1
        self.g.ingest('rt/continuity',dict(protocol=1,session='a',sequence=self.source_seq,
            generation=generation,source=source,at=self.now))
        self.g.ingest('rt/lowstate',{'motor_state':[dict(q=0.,dq=0.,motorstate=0)]*29,
            'imu_state':dict(quaternion=[1.,0.,0.,0.])})
        self.g.ingest('rt/secondary_imu',dict(quaternion=[1.,0.,0.,0.]))

    def request(self, route='/api/controller', **kw):
        self.seq+=1
        return self.g.request(route,dict(client='test',seq=self.seq,epoch=self.g.epoch,ticket=self.g.ticket(),**kw))

    def active(self):
        self.feedback();self.now+=.05;self.feedback()
        self.request(action='prepare_pose');self.log='Init Done'
        self.request(action='start_policy');self.log+='\nBumpless policy handoff complete'
        self.g.tick();self.assertEqual(self.g.phase,'active')

    def test_no_implicit_start_and_existing_interface_works(self):
        self.feedback();self.g.tick();self.assertEqual(self.launches,0)
        with self.assertRaises(ValueError):self.request(action='prepare_pose')
        self.now+=.05;self.feedback();self.request(action='prepare_pose')
        self.assertEqual(self.g.phase,'loading');self.assertFalse(self.g.start_pending)

    def test_fresh_standby_preserves_policy_and_temporarily_blocks_new_motion(self):
        self.active();self.now+=.06;self.feedback('standby',2);self.g.tick()
        self.assertEqual(self.g.phase,'active');self.assertEqual(self.proc.kills,0)
        with self.assertRaises(ValueError):self.request('/api/control',move='forward')
        self.request('/api/control',stop=True)
        self.now+=.05;self.feedback('standby',2)
        self.request('/api/control',move='forward')
        self.assertEqual(self.g.last_packet['speed'],.5)
        self.assertEqual(self.launches,1)

    def test_total_loss_original_deadline_and_estop_unchanged(self):
        self.active();self.now+=.501;self.g.tick()
        self.assertEqual(self.proc.kills,1);self.assertEqual(self.g.phase,'error')
        self.setUp();self.active();self.now+=.06;self.feedback(None,2)
        self.request(action='estop')
        self.assertEqual(self.proc.kills,1);self.assertEqual(self.g.phase,'stopped')


if __name__=='__main__':unittest.main()
