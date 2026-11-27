"""Status gates drain received feedback without refreshing its source timestamps."""
from copy import deepcopy
import unittest
from unittest.mock import Mock
from onboardmimic.backends.sonic.feedback import FeedbackInbox
from tests.runtime import test_native_lifecycle as lifecycle_fixture


class StatusFeedbackRefreshTests(unittest.TestCase):
    def setUp(self):
        self.f=lifecycle_fixture.NativeTest();self.f.setUp();self.f.activate()
        self.g=self.f.g;self.g.transport_driven=True;self.g.require_continuity=True
        self.g.robot.health.ingest(dict(protocol=1,session='test',sequence=1,generation=1,
            at=self.f.now-.1,source='primary'),self.f.now-.1)
        self.g.feedback_inbox=FeedbackInbox(self.g.robot.state_topics,clock=lambda:self.f.now)
        def native(runtime):
            return dict(record_ready=runtime['feedback_ready'],native_consumption_fresh=True,
                        reason='' if runtime['feedback_ready'] else 'runtime_feedback_unavailable')
        self.g.native_consumption=Mock(status=Mock(side_effect=native))

    def enqueue(self, age=.005, dq=0., fault=0):
        for topic in ('rt/lowstate','rt/secondary_imu'):
            packet=deepcopy(self.g.latest[topic][1])
            if topic=='rt/lowstate':
                for motor in packet['motor_state']:motor.update(dq=dq,motorstate=fault)
            self.g.feedback_inbox.put(topic,packet,self.f.now-age,
                decoded_at=self.f.now-.001,decode_cpu_ns=1000)

    def test_capture_applies_waiting_packets_before_native_readiness(self):
        self.f.now+=.08;self.enqueue()
        self.assertFalse(self.g.feedback_ready())
        value=self.g.query_status()['lab']
        self.assertTrue(value['record_ready'])
        gate=value['recording_validation']
        self.assertAlmostEqual(gate['captured_feedback_ages_ms']['rt/lowstate'],5.)
        applied=gate['captured_feedback_pipeline']['applied']['rt/lowstate']
        self.assertEqual(applied['received_at'],self.f.now-.005)
        self.assertEqual(applied['decoded_at'],self.f.now-.001)
        self.assertEqual(applied['applied_at'],self.f.now)
        self.assertEqual(self.f.proc.kills,0)

    def test_final_gate_applies_arrivals_during_render(self):
        view=self.g._capture_status();value=view._render_status()
        self.f.now+=.06;self.enqueue()
        self.f.now+=.02  # Wait in the inbox, without a control tick.
        result=self.g._validate_status(view,value)['lab']
        self.assertTrue(result['record_ready'])
        gate=result['recording_validation'];applied=gate['feedback_pipeline']['applied']['rt/lowstate']
        self.assertAlmostEqual(gate['feedback_ages_ms']['rt/lowstate'],25.)
        self.assertAlmostEqual(applied['queue_wait_ms'],20.)
        self.assertEqual(view.latest['rt/lowstate'][0],100.)

    def test_actual_stale_packet_stays_stale_after_application(self):
        view=self.g._capture_status();value=view._render_status()
        self.f.now+=.08;self.enqueue(age=.06)
        result=self.g._validate_status(view,value)['lab']
        self.assertFalse(result['record_ready'])
        self.assertEqual(result['reason'],'runtime_feedback_unavailable')
        self.assertAlmostEqual(result['recording_validation']['feedback_ages_ms']['rt/lowstate'],60.)
        self.assertEqual(self.g.latest['rt/lowstate'][0],self.f.now-.06)

    def test_new_fault_or_speed_is_not_hidden_by_healthy_snapshot(self):
        for dq,fault,reason in ((0.,1,'recording_joint_fault'),(13.,0,'recording_joint_overspeed')):
            self.setUp()
            view=self.g._capture_status();value=view._render_status()
            self.f.now+=.03;self.enqueue(dq=dq,fault=fault)
            result=self.g._validate_status(view,value)['lab']
            self.assertFalse(result['record_ready']);self.assertEqual(result['reason'],reason)

    def test_source_change_still_requires_stabilization_and_new_samples(self):
        view=self.g._capture_status();value=view._render_status()
        self.f.now+=.02;self.enqueue()
        self.g.feedback_inbox.put('rt/continuity',dict(protocol=1,session='test',sequence=2,
            generation=2,at=self.f.now-.003,source='standby'),self.f.now-.003)
        result=self.g._validate_status(view,value)['lab']
        self.assertFalse(result['record_ready'])
        self.assertEqual(result['reason'],'runtime_feedback_unavailable')
        self.assertEqual(self.g.robot.health.status['source'],'standby')

    def test_cached_feedback_failure_is_recaptured_when_packets_arrive(self):
        self.f.now+=.08
        self.assertFalse(self.g.query_status()['lab']['record_ready'])
        self.enqueue()
        self.assertTrue(self.g.query_status()['lab']['record_ready'])
        self.assertEqual(self.g.native_consumption.status.call_count,2)
