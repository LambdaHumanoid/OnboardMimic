"""Recording may recheck a stale observation, but never accept it as permission."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch
from onboardmimic_lab.adapters.reference_capture import recording_status
from onboardmimic_lab.contracts import fingerprint
from test_sonic_native import fixtures


class RecordingReadinessTest(unittest.TestCase):
    def setUp(self):
        self.spec, _ = fixtures()
        self.good = dict(state='active', input_mode='pose', mode_switch_pending=False,
                         lab=dict(record_ready=True, reason='', session_id='session',
                                  contract_sha256=fingerprint(self.spec)))
        self.stale = deepcopy(self.good)
        self.stale['lab'].update(record_ready=False, reason='native_consumption_stale_or_warming')

    def check(self, values):
        read = Mock(side_effect=values); observations = []
        with patch('onboardmimic_lab.adapters.reference_capture.time.sleep'):
            result = recording_status(read, self.spec, 'session', observations.append)
        return result, observations

    def test_transient_requires_a_new_fully_ready_observation(self):
        result, observations = self.check([self.stale, self.good])
        self.assertIs(result, self.good)
        self.assertEqual(observations, [self.stale, self.good])

    def test_persistent_stale_is_not_accepted(self):
        with self.assertRaisesRegex(ValueError, 'native_consumption_stale_or_warming'):
            self.check([self.stale]*5)

    def test_session_contract_and_faults_are_not_retried(self):
        for field, value, reason in [('session_id','other','session changed'),
                                     ('contract_sha256','x','contract changed'),
                                     ('reason','protected_output_unavailable','protected_output_unavailable')]:
            bad = deepcopy(self.stale); bad['lab'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, reason):
                self.check([bad])  # A second read would fail the Mock.

    def test_fault_during_recheck_is_not_hidden_by_later_recovery(self):
        bad = deepcopy(self.stale); bad['lab']['reason'] = 'native_consumption_fault'
        with self.assertRaisesRegex(ValueError, 'native_consumption_fault'):
            self.check([self.stale, bad])

    def test_slow_recovery_cannot_extend_deadline(self):
        with patch('onboardmimic_lab.adapters.reference_capture.time.monotonic', side_effect=[0, 0, .101]), \
             patch('onboardmimic_lab.adapters.reference_capture.time.sleep'), \
             self.assertRaisesRegex(ValueError, 'readiness lost'):
            recording_status(Mock(side_effect=[self.stale,self.good]), self.spec, 'session', lambda _:None)

    def expired_snapshot(self):
        value=deepcopy(self.good)
        value['lab'].update(record_ready=False,reason='recording_status_snapshot_expired',
            recording_validation=dict(failures=['recording_status_snapshot_expired'],native_record_ready=True,native_reason=''))
        return value

    def test_snapshot_expiry_gets_one_fresh_query(self):
        expired=self.expired_snapshot()
        result,observations=self.check([expired,self.good])
        self.assertIs(result,self.good)
        self.assertEqual(len(observations),2)
        with self.assertRaisesRegex(ValueError,'recording_status_snapshot_expired'):
            self.check([expired,expired])

    def test_snapshot_with_fault_or_unverified_native_evidence_is_not_retried(self):
        for change in (dict(failures=['recording_status_snapshot_expired','recording_joint_fault']),
                       dict(native_record_ready=False),dict(native_reason='native_consumption_fault')):
            bad=self.expired_snapshot();bad['lab']['recording_validation'].update(change)
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'readiness lost'):
                self.check([bad])

    def test_slow_snapshot_query_budget_starts_after_first_response_only(self):
        expired=self.expired_snapshot()
        for returned,success in ((.35,True),(.41,False)):
            with patch('onboardmimic_lab.adapters.reference_capture.time.monotonic',side_effect=[0,.3,returned]):
                read=Mock(side_effect=[expired,self.good])
                if success:self.assertIs(recording_status(read,self.spec,'session',lambda _:None),self.good)
                else:
                    with self.assertRaisesRegex(ValueError,'readiness lost'):
                        recording_status(read,self.spec,'session',lambda _:None)
