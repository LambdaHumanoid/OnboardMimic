from export_test_helpers import wait_export
"""Two action spaces through contracts, capture, video export and replay dispatch."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from onboardmimic_lab.contracts import validate_spec, validate_sample, fingerprint, ReferencePolicy
from onboardmimic_lab.collection import Collector, validate_config
from onboardmimic_lab.dataset import read_aligned
from onboardmimic_lab.reference_dataset import RawEpisode, align
from onboardmimic_lab.reference_replay import prepare_plan, validate_plan
from onboardmimic_lab.lerobot_replay import validate_plan as validate_physical
from onboardmimic_lab.simulation import run_simulation, register_simulator
from onboardmimic_lab.video_timing import MpegTsPtsExtractor
from test_collection import make_video

EXAMPLE = Path(__file__).resolve().parents[1]/'examples/reference-collection.fixture.json'


def spec(dimension=3):
    s = json.loads(EXAMPLE.read_text())['controller_spec']
    for key in ('state', 'reference', 'control_output'):
        for field in s[key].values():
            field['shape'][-1] = dimension
            field['joint_names'] = ['joint_'+str(i) for i in range(dimension)]
    return validate_spec(s)


def sample(s, index, stamp=None):
    stamp = stamp if stamp is not None else 1_000_000_000+index*s['period_ns']
    dim = s['reference']['q_target']['shape'][0]
    return dict(contract_sha256=fingerprint(s), session_id='fixture-session', reference_id='ref-'+str(index),
                reference_evidence='consumed', index=index, timestamp_monotonic_ns=stamp,
                timestamp_realtime_ns=stamp+1_000_000_000, state_history_ns=[stamp-10_000_000, stamp],
                state={'q':[[0.0]*dim,[0.01]*dim]}, reference={'q_target':[0.02]*dim},
                control_output={'q_target':[0.02]*dim})


def status(s):
    return dict(state='active', input_mode='teleoperation', lab=dict(record_ready=True,
                contract_sha256=fingerprint(s), session_id='fixture-session'))


class ReferenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_capture_gap_preserves_publication_and_write_evidence(self):
        s=spec();raw=RawEpisode(self.root/'audit',dict(controller_spec=s))
        for index,sequence in ((0,10),(2,11)):
            row=sample(s,index)
            row['capture_diagnostics']=dict(publication_session='test',publication_sequence=sequence)
            raw.controller(row,row['timestamp_monotonic_ns'])
        raw.finish()
        events=[json.loads(x) for x in (raw.root/'runtime.jsonl').read_text().splitlines()]
        gap=next(x for x in events if x['event']=='controller_gap')
        self.assertEqual((gap['previous_index'],gap['index']),(0,2))
        self.assertEqual(gap['diagnostics']['publication_sequence'],11)
        writes=[x for x in events if x['event']=='controller_write_returned']
        self.assertEqual([x['index'] for x in writes],[0,2])
        self.assertTrue(all(x['boundary']=='buffered_write_not_fsync' for x in writes))

    def test_semantics_not_just_dimension_and_history_validation(self):
        s = spec(); validate_sample(sample(s, 0), s)
        for mutate in (lambda x:x['reference']['q_target'].append(0),
                       lambda x:x['reference']['q_target'].__setitem__(0, float('nan')),
                       lambda x:x['state_history_ns'].__setitem__(0, 0),
                       lambda x:x.update(contract_sha256='0'*64)):
            bad = sample(s, 0); mutate(bad)
            with self.assertRaises(ValueError): validate_sample(bad, s)
        model = ReferencePolicy(lambda obs:[{'q_target':[1,2,3]}], s, s)
        self.assertEqual(model({})[0]['q_target'], [1,2,3])
        for key, value in [('unit', 'degree'), ('joint_names', ['joint_2','joint_1','joint_0'])]:
            changed = deepcopy(s); changed['reference']['q_target'][key] = value
            with self.assertRaises(ValueError): ReferencePolicy(lambda obs:[], changed, s)

    def test_session_restart_and_unconsumed_reference_never_becomes_label(self):
        s = spec(); raw = RawEpisode(self.root/'raw', dict(task='fixture', controller_spec=s))
        self.addCleanup(lambda:raw.finish())
        for i in range(4):
            row = sample(s, i)
            if i == 1: row['reference_evidence'] = 'submitted'
            raw.controller(row, row['timestamp_monotonic_ns'])
        bad = sample(s, 4); bad['session_id'] = 'new'
        with self.assertRaises(ValueError): raw.controller(bad, bad['timestamp_monotonic_ns'])
        raw.finish()
        camera = [dict(monotonic_ns=sample(s,i)['timestamp_monotonic_ns'], sequence=i,
                       pts_90khz=i*1800, image=str(i)+'.jpg') for i in range(4)]
        data, rejected = align(raw.root, camera, s)
        self.assertEqual(rejected, 1)
        self.assertEqual([r['sample']['index'] for r in data], [0,2,3])
        self.assertEqual([r['segment_index'] for r in data], [0,1,1])

    def wait(self, collector, state):
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            value = collector.status()
            if value['state'] == state: return value
            if value['state'] == 'failed': self.fail(str(value))
            time.sleep(.01)
        self.fail(str(collector.status()))

    def test_two_dimensions_lifecycle_save_plan_policy_and_simulator_dispatch(self):
        video = make_video(self.root/'input.ts')
        for dim in (3, 7):
            s = spec(dim)
            config = validate_config(dict(capture_adapter='reference-v1', root=str(self.root/str(dim)),
                controller_spec=s, same_host=True, telemetry_endpoint='tcp://127.0.0.1:16688'))
            def worker(raw):
                raw.video(video)
                for i, pts in enumerate(MpegTsPtsExtractor().feed(video)):
                    row = sample(s, i); raw.controller(row, row['timestamp_monotonic_ns'])
                    raw.append('camera.jsonl', dict(monotonic_ns=row['timestamp_monotonic_ns'],
                               pts_90khz=pts, sequence=i))
                collector.stop_event.wait(5)
            collector = Collector(config, lambda:status(s), worker=worker)
            try:
                collector.command(dict(action='start', task='generic joints'))
                collector.command(dict(action='stop')); pending = self.wait(collector, 'review')
                collector.command(dict(action='save')); self.wait(collector, 'saved')
                path = Path(config['root'])/'episodes'/pending['episode_id']
                wait_export(path)
                summary, data = read_aligned(path); self.assertEqual(len(data), 20)
                plan = prepare_plan(path); validate_plan(plan)
                self.assertEqual(len(plan['frames'][0]['sample']['reference']['q_target']), dim)
                self.assertEqual(plan['frames'][0]['offset_ns'], 0)
                with self.assertRaises(ValueError): run_simulation(plan, {'controller_spec':s}, self.root/'sim')
                with self.assertRaises(ValueError): validate_physical(plan)
                # Distinct plugin id for each dimension. This is not physics validation.
                execution = 'fixture.dispatch.'+str(dim)
                plan = deepcopy(plan); plan['controller_spec']['execution_id'] = execution
                plan['contract_sha256'] = fingerprint(plan['controller_spec'])
                for frame in plan['frames']: frame['sample']['contract_sha256'] = plan['contract_sha256']
                def simulate(plan, config, output, video=True):
                    return {'fixture_only':True,'frames':len(plan['frames']),'physical_validation':False}
                register_simulator(execution, simulate)
                result = run_simulation(plan, {'controller_spec':plan['controller_spec']}, self.root/'sim')
                self.assertTrue(result['fixture_only']); self.assertFalse(result['physical_validation'])
                # Shape-preserving artifact mutation is still rejected.
                bad = deepcopy(plan); bad['controller_spec']['artifacts']['fixture'] = '0'*64
                with self.assertRaises(ValueError): validate_plan(bad)
                image = path/'camera.ts'
                image.write_bytes(b'corrupted image')
                with self.assertRaises(ValueError): read_aligned(path)
            finally: collector.close()

    def test_invalid_config_and_unconfirmed_readiness(self):
        config = json.loads(EXAMPLE.read_text()); config['root'] = str(self.root/'data')
        for change in ({'same_host':False}, {'telemetry_endpoint':'tcp://remote:16688'}, {'capture_adapter':'guess'}):
            with self.assertRaises(ValueError): validate_config(dict(config, **change))
        collector = Collector(config, lambda:dict(state='active', input_mode='teleoperation'))
        try:
            with self.assertRaises(ValueError): collector.command(dict(action='start', task='no evidence'))
        finally: collector.close()

    @patch('onboardmimic_lab.adapters.reference_capture.MpegTsPtsExtractor', side_effect=AssertionError('camera timestamps must not be parsed twice'))
    def test_loopback_standard_telemetry_and_gopro_no_sonic_or_pico(self, unused_parser):
        import socket
        import zmq
        from onboardmimic_lab.adapters.reference_capture import TOPIC
        video = make_video(self.root/'transport.ts', frames=100)
        groups, group = [], bytearray()
        for start in range(0, len(video), 188):
            packet = video[start:start+188]
            if MpegTsPtsExtractor._packet_pts(packet) is not None and group:
                groups.append(bytes(group)); group.clear()
            group.extend(packet)
        if group: groups.append(bytes(group))
        context = zmq.Context(); publisher = context.socket(zmq.PUB)
        publisher.setsockopt(zmq.LINGER, 0)
        port = publisher.bind_to_random_port('tcp://127.0.0.1')
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1', 0)); camera_port = udp.getsockname()[1]; udp.close()
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s = spec(7)
        config = validate_config(dict(capture_adapter='reference-v1',root=str(self.root/'live'),
                controller_spec=s,same_host=True,telemetry_endpoint='tcp://127.0.0.1:'+str(port),
                camera_port=camera_port,max_duration_s=1))
        collector = Collector(config, lambda:status(s))
        try:
            collector.command(dict(action='start',task='loopback joints'))
            for i, group in enumerate(groups):
                # Camera arrives before reference; causal alignment must never look forward.
                for offset in range(0, len(group), 1316):
                    udp.sendto(group[offset:offset+1316], ('127.0.0.1',camera_port))
                row = sample(s, i, time.monotonic_ns())
                row['timestamp_realtime_ns'] = time.time_ns()
                publisher.send_multipart([TOPIC,json.dumps(row).encode()])
                if collector.status()['state'] in ('review','failed'): break
                time.sleep(.02)
            self.assertEqual(collector.status()['state'],'recording')
            self.assertNotIn('max_duration_s',config)
            collector.command(dict(action='stop'))
            result = self.wait(collector,'review')
            self.assertGreater(result['counts']['controller.jsonl'], 10)
            collector.command(dict(action='save')); self.wait(collector,'saved')
            wait_export(Path(config['root'])/'episodes'/result['episode_id'])
            summary, data = read_aligned(Path(config['root'])/'episodes'/result['episode_id'])
            self.assertGreater(len(data),10)
            self.assertEqual(len(data[0]['sample']['reference']['q_target']),7)
            self.assertTrue(all(r['camera_age_ns'] >= 0 for r in data))
        finally:
            collector.close();udp.close();publisher.close();context.term()
