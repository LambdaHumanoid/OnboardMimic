import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from onboardmimic.backends.sonic.native.spikes import SpikeRecorder
from onboardmimic.backends.sonic.native.audit import Audit


def attempt(index=1, duration=21_800_000, at=1_000_000_000):
    return dict(stage='received',at_ns=at,source='native',kind=0,index=index,
                native_session='test',source_ns=at-30_000_000,finished_ns=at-30_000_000+duration,
                timing=dict(input_copy_wall_ns=50_000,launch_wall_ns=40_000,
                            output_copy_wall_ns=20_000,synchronize_wall_ns=duration-110_000),
                buffer_replacement=dict(index=index-1,selected=True),audit_drops=0)


def feed(s,row): s.observe(row,json.dumps(row)+'\n')


class SpikeTests(unittest.TestCase):
    def test_no_gap_still_preserves_spike_before_after_and_coalesces(self):
        with tempfile.TemporaryDirectory() as d:
            s=SpikeRecorder(d)
            feed(s,attempt(1,2_000_000,800_000_000))
            feed(s,attempt(2,21_800_000,1_000_000_000))
            feed(s,dict(stage='received',source='bridge',kind=2,index=2,at_ns=1_010_000_000))
            feed(s,attempt(3,22_000_000,1_020_000_000))
            feed(s,attempt(4,2_000_000,1_201_000_000))
            folder=next((Path(d)/'timing-incidents').iterdir())
            snap=json.loads((folder/'snapshot.json').read_text())
            events=[json.loads(x) for x in (folder/'events.jsonl').read_text().splitlines()]
            self.assertEqual([x['index'] for x in events],[1,2,2,3])
            self.assertEqual(len(snap['triggers']),2)
            self.assertTrue(snap['post_window_complete'])
            self.assertEqual(events[1]['timing']['input_copy_wall_ns'],50_000)
            self.assertEqual(s.latest['state'],'saved')

    def test_thresholds_control_and_wrong_source(self):
        self.assertIsNone(SpikeRecorder.trigger(attempt(duration=10_000_000)))
        x=attempt(duration=2_000_000)
        x['timing']['control_stages_ns']=[x['source_ns']-20_000_000]
        self.assertEqual(SpikeRecorder.trigger(x)['control_wall_ns'],22_000_000)
        x['source']='bridge'
        self.assertIsNone(SpikeRecorder.trigger(x))

    def test_transport_delay_without_inference_spike_or_missing_index(self):
        with tempfile.TemporaryDirectory() as d:
            s=SpikeRecorder(d)
            normal=attempt(duration=2_000_000,at=900_000_000)
            feed(s,normal)
            delayed=dict(stage='received',source='bridge',kind=2,at_ns=1_000_000_000,
                         published_monotonic_ns=950_000_000,received_ns=999_000_000)
            feed(s,delayed)
            joined=dict(stage='joined',at_ns=1_005_000_000,source_ns=940_000_000,
                        received_ns=999_000_000,decoded_ns=1_000_000_000,
                        ingest_started_ns=1_001_000_000,ingest_finished_ns=1_004_000_000,
                        thread_cpu_ns=2_000_000,index=2)
            feed(s,joined);s.finish(True)
            snap=json.loads(next((Path(d)/'timing-incidents').glob('*/snapshot.json')).read_text())
            self.assertEqual([t['kind'] for t in snap['triggers']],
                             ['telemetry_receive_delay','telemetry_join_delay'])
            self.assertEqual(snap['triggers'][1]['event']['thread_cpu_ns'],2_000_000)
            self.assertTrue(snap['post_window_complete'])
        self.assertIsNone(SpikeRecorder.trigger(dict(stage='joined',at_ns=100_000_000,source_ns=60_000_000)))
        self.assertIsNone(SpikeRecorder.trigger(dict(stage='received',source='bridge',at_ns=100_000_000,published_monotonic_ns=101_000_000)))

    def test_shutdown_partial_and_later_incident_kept(self):
        with tempfile.TemporaryDirectory() as d:
            s=SpikeRecorder(d);feed(s,attempt());s.finish()
            first=s.latest['id'];feed(s,attempt(at=2_000_000_000));s.finish()
            self.assertNotEqual(first,s.latest['id'])
            self.assertEqual(len(list((Path(d)/'timing-incidents').iterdir())),2)
            self.assertFalse(s.latest['post_window_complete'])

    def test_disk_failure_is_observable_and_retry_possible(self):
        with tempfile.TemporaryDirectory() as d:
            s=SpikeRecorder(d)
            with patch.object(Path,'mkdir',side_effect=OSError('full')):feed(s,attempt())
            self.assertEqual(s.latest['state'],'failed')
            feed(s,attempt(at=2_000_000_000));s.finish()
            self.assertEqual(s.latest['state'],'saved')

    def test_pre_window_bound_is_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            s=SpikeRecorder(d);s.MAX_PRE_ROWS=2
            for i in range(4):feed(s,attempt(i,2_000_000,900_000_000+i))
            feed(s,attempt());s.finish()
            snap=json.loads(next((Path(d)/'timing-incidents').glob('*/snapshot.json')).read_text())
            self.assertTrue(snap['pre_window_truncated'])

    def test_audit_rotation_does_not_erase_spike(self):
        with tempfile.TemporaryDirectory() as d:
            a=Audit(Path(d)/'consumption-test.jsonl',max_bytes=1024)
            row=attempt();row.pop('at_ns');stage=row.pop('stage');a.emit(stage,**row)
            for i in range(100):a.emit('received',source='bridge',index=i)
            a.close()
            self.assertFalse(a.error)
            snap=json.loads(next((Path(d)/'timing-incidents').glob('*/snapshot.json')).read_text())
            self.assertEqual(snap['triggers'][0]['decoder_wall_ns'],21_800_000)
            self.assertEqual(a.spikes.latest['state'],'saved')
