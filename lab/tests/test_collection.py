import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock

from onboardmimic_lab.collection import Collector
from onboardmimic_lab.dataset import RawEpisode, align, export_episode, verify_raw, rows, write_json, read_aligned
from onboardmimic_lab.reference_replay import prepare_plan
from onboardmimic_lab.video_timing import MpegTsPtsExtractor
from onboardmimic_lab.export_jobs import finish_export
from export_test_helpers import wait_export

ROOT = Path(__file__).resolve().parents[1]


def fixture_spec():
    from test_reference import spec
    return spec()


def fixture_config(root):
    return dict(root=str(root),capture_adapter='reference-v1',controller_spec=fixture_spec(),
                same_host=True,telemetry_endpoint='tcp://127.0.0.1:16691',camera_port=18554)


def sample(index):
    from test_reference import sample as reference_sample
    return reference_sample(fixture_spec(),index)


def active():
    from test_reference import status
    return status(fixture_spec())


def make_video(path, frames=20):
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                    "testsrc2=size=64x48:rate=50", "-frames:v", str(frames), "-c:v", "libx264",
                    "-threads", "1", "-g", "10", "-bf", "0", "-pix_fmt", "yuv420p",
                    "-muxdelay", "0", "-f", "mpegts", str(path)], check=True, capture_output=True)
    return path.read_bytes()


def fill(raw, video):
    pts = MpegTsPtsExtractor().feed(video)
    raw.video(video)
    for i, value in enumerate(pts):
        s = sample(i)
        raw.controller(s, s["timestamp_monotonic_ns"]+1_000_000)
        raw.append("camera.jsonl", dict(sequence=i+1, pts_90khz=value,
                                       monotonic_ns=s["timestamp_monotonic_ns"], realtime_ns=s["timestamp_realtime_ns"]))
    raw.append("runtime.jsonl", dict(robot_config={"fixture": True}))


class DatasetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_real_video_export_matches_token_and_image_pts(self):
        video = make_video(self.root/"input.ts")
        raw = RawEpisode(self.root/"ep", dict(task="fixture", controller_spec=fixture_spec()))
        fill(raw, video)
        raw.finish()
        finish_export(raw.root)
        summary,frames=read_aligned(raw.root)
        self.assertEqual(summary['rows'],20)
        self.assertEqual(summary['segments'],1)
        self.assertEqual(frames[5]['sample']['reference'],sample(5)['reference'])
        self.assertEqual(frames[5]['camera_age_ns'],0)
        self.assertEqual(frames[5]['image']['frame_index'],5)
        with self.assertRaises(ValueError):
            export_episode(raw.root)

    def test_causal_matching_rejects_future_and_splits_gaps(self):
        raw = RawEpisode(self.root/"ep", dict(task="fixture", controller_spec=fixture_spec()))
        for i in range(8):
            raw.controller(sample(i), sample(i)["timestamp_monotonic_ns"]+1_000_000)
        raw.finish()
        frames = [dict(monotonic_ns=1_010_000_000, sequence=1, pts_90khz=0, image="first.jpg"),
                  dict(monotonic_ns=1_140_000_000, sequence=2, pts_90khz=100, image="next.jpg")]
        result, rejected = align(raw.root, frames, fixture_spec())
        self.assertEqual(rejected, 3)  # first is future, samples 5/6 are too old
        self.assertEqual(result[0]["sample"]["index"], 1)
        self.assertEqual(result[-1]["segment_index"], 1)
        self.assertEqual(result[-1]["sample"]["timestamp_monotonic_ns"], 1_140_000_000)

    def test_corrupt_raw_video_refused(self):
        raw = RawEpisode(self.root/"ep", dict(task="fixture", controller_spec=fixture_spec()))
        raw.finish()
        (raw.root/"camera.ts").write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            verify_raw(raw.root)


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.encoder, self.decoder = self.root/"encoder", self.root/"decoder"
        self.encoder.write_bytes(b"fixture encoder")
        self.decoder.write_bytes(b"fixture decoder")
        self.config = fixture_config(self.root/'data')
        self.runtime = Mock(return_value=active())
        self.collector = None

    def tearDown(self):
        if self.collector:
            self.collector.close()

    def wait(self, state):
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            actual = self.collector.status()
            if actual["state"] == state:
                return actual
            time.sleep(.01)
        self.fail(str(actual))

    def test_task_groups_episodes_and_survives_restart_with_optional_metadata(self):
        video = make_video(self.root/'task-fixture.ts')
        def worker(raw):
            fill(raw, video)
            self.collector.stop_event.wait(3)
        self.collector = Collector(self.config, self.runtime, worker=worker)
        task_id = None; episodes = []
        for person in ('', '李四'):
            request = dict(action='start', task_id=task_id) if task_id else dict(action='start')
            request['collector_name'] = person
            if not task_id:request['task_name']='搬运'
            result = self.collector.command(request);task_id = result['task_id']
            self.collector.command(dict(action='stop'));result = self.wait('review')
            self.collector.command(dict(action='save'));self.wait('saved')
            episodes.append(result['episode_id'])
            path = self.root/'data/episodes'/result['episode_id']
            wait_export(path)
            for file in ('manifest.json','aligned/episode.json'):
                metadata = json.loads((path/file).read_text())
                self.assertEqual(metadata['task_id'],task_id)
                self.assertEqual(metadata['task_name'],'搬运')
                self.assertEqual(metadata['episode_index'],len(episodes)-1)
                self.assertEqual(metadata['task'],'')
                self.assertEqual(metadata['collector_name'],person)
        self.assertNotEqual(*episodes)
        self.assertEqual(len(self.collector.status()['tasks']),1)
        self.collector.close();self.collector=None
        self.collector=Collector(self.config,self.runtime,worker=worker)
        self.assertEqual(self.collector.status()['task_id'],task_id)
        with self.assertRaises(ValueError):self.collector.command(dict(action='start',task_id='../bad'))
        result=self.collector.command(dict(action='start',task_description='拾取',collector_name='张三'))
        self.assertNotEqual(result['task_id'],task_id)
        self.assertEqual(result['episode_index'],0)
        self.collector.command(dict(action='stop'));self.wait('review')
        self.collector.command(dict(action='discard'))
        row=next(t for t in self.collector.status()['tasks'] if t['id']==result['task_id'])
        self.assertEqual(row['description'],'拾取');self.assertNotIn('collector_name',row)
        metadata=json.loads((self.root/'data/discarded'/result['episode_id']/'manifest.json').read_text())
        self.assertEqual(metadata['collector_name'],'张三')

    def test_start_stop_save_and_recoverable_discard(self):
        video = make_video(self.root/"fixture.ts")
        def worker(raw):
            fill(raw, video)
            self.collector.stop_event.wait(3)
        self.collector = Collector(self.config, self.runtime, worker=worker)
        self.collector.command(dict(action="start", task="pick"))
        with self.assertRaises(ValueError):
            self.collector.command(dict(action="start", task="duplicate"))
        self.collector.command(dict(action="stop"))
        result = self.wait("review")
        self.collector.command(dict(action="save"))
        self.wait("saved")
        saved = self.root/"data/episodes"/result["episode_id"]
        wait_export(saved)
        self.assertEqual(verify_raw(saved)["status"], "saved")
        self.assertTrue((saved/"aligned/frames.jsonl").is_file())
        aligned, data = read_aligned(saved)
        plan=prepare_plan(saved)
        self.assertEqual(len(plan['frames']),aligned['rows'])
        self.assertEqual(plan['frames'][5]['sample'],data[5]['sample'])
        image=saved/'camera.ts'
        image.write_bytes(b'corruption')
        with self.assertRaises(ValueError):
            read_aligned(saved)
        self.collector.command(dict(action="start", task="discard me"))
        self.collector.command(dict(action="stop"))
        result = self.wait("review")
        self.collector.command(dict(action="discard"))
        discarded = self.root/"data/discarded"/result["episode_id"]
        self.assertTrue(discarded.exists())
        self.assertEqual(json.loads((discarded/"manifest.json").read_text())["status"], "discarded")

    def test_reject_inactive_or_unready_reference_capture(self):
        self.collector = Collector(self.config, self.runtime)
        for change in (dict(input_mode='planner'),dict(state='stopped'),dict(mode_switch_pending=True),
                       dict(lab=dict(record_ready=False))):
            self.runtime.return_value = dict(active(), **change)
            with self.assertRaises(ValueError):
                self.collector.command(dict(action="start", task="pick"))
        self.assertEqual(self.collector.status()["pending"], [])

    def test_worker_failure_cannot_save_and_can_recover(self):
        def fail(raw):
            raise ValueError("camera lost")
        self.collector = Collector(self.config, self.runtime, worker=fail)
        self.collector.command(dict(action="start", task="pick"))
        state = self.wait("failed")
        with self.assertRaises(ValueError):
            self.collector.command(dict(action="save"))
        self.collector.close()
        self.collector = Collector(self.config, self.runtime)
        self.assertEqual(len(self.collector.status()["pending"]), 1)
        self.collector.command(dict(action="select", id=state["episode_id"]))
        self.assertEqual(self.collector.status()["state"], "failed")
        self.collector.command(dict(action="discard"))

    def test_process_lock_and_crash_recovery(self):
        self.collector = Collector(self.config, self.runtime)
        with self.assertRaises(ValueError):
            Collector(self.config, self.runtime)
        raw = RawEpisode(self.root/"data/pending"/("a"*32), dict(task="interrupted", controller_spec=fixture_spec()))
        for f in raw.streams.values():
            f.close()
        self.collector.close()
        self.collector = Collector(self.config, self.runtime)
        self.collector.command(dict(action="select", id="a"*32))
        self.assertEqual(self.collector.status()["state"], "failed")
        with self.assertRaises(ValueError):
            self.collector.command(dict(action="save"))


    def test_loopback_reference_subscriber_and_gopro_udp(self):
        self.exercise()

    def test_live_readiness_loss_marks_capture_failed(self):
        self.exercise(failure="encoder")

    def test_live_camera_loss_marks_capture_failed(self):
        self.exercise(failure="camera")

    def exercise(self, failure=None):
        import zmq
        video = make_video(self.root/"stream.ts", frames=150)
        # Split the valid TS on each video PES start to pace it at 50 Hz.
        groups, group = [], bytearray()
        for start in range(0, len(video), 188):
            packet = video[start:start+188]
            if MpegTsPtsExtractor._packet_pts(packet) is not None and group:
                groups.append(bytes(group))
                group.clear()
            group.extend(packet)
        if group:
            groups.append(bytes(group))
        context = zmq.Context()
        publisher = context.socket(zmq.PUB)
        publisher.setsockopt(zmq.LINGER, 0)
        port = publisher.bind_to_random_port("tcp://127.0.0.1")
        reservation = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        reservation.bind(("127.0.0.1", 0))
        camera_port = reservation.getsockname()[1]
        reservation.close()
        self.config.update(telemetry_endpoint="tcp://127.0.0.1:%d" % port, camera_port=camera_port,
                           max_duration_s=2 if failure else .6)
        self.collector = Collector(self.config, self.runtime)
        self.collector.command(dict(action="start", task="synthetic loopback"))
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for i, group in enumerate(groups):
                now, wall = time.monotonic_ns(), time.time_ns()
                s = sample(i)
                s.update(timestamp_monotonic_ns=now,timestamp_realtime_ns=wall,state_history_ns=[now-10_000_000,now])
                if failure=='encoder' and i>15:
                    self.runtime.return_value=active()
                    self.runtime.return_value['lab']['record_ready']=False
                publisher.send_multipart([b'oml/controller/v1',json.dumps(s).encode()])
                if failure != "camera" or i <= 15:
                    for offset in range(0, len(group), 1316):
                        udp.sendto(group[offset:offset+1316], ("127.0.0.1", camera_port))
                if self.collector.status()["state"] in ("review", "failed"):
                    break
                time.sleep(.02)
            if failure:
                result = self.wait("failed")
                self.assertIn("readiness lost" if failure == "encoder" else "freshness", result["error"])
                with self.assertRaises(ValueError):
                    self.collector.command(dict(action="save"))
                return
            self.assertEqual(self.collector.status()["state"],"recording")
            self.collector.command(dict(action="stop"))
            result = self.wait("review")
            self.assertGreater(result["counts"]["controller.jsonl"], 10)
            self.collector.command(dict(action="save"))
            self.wait("saved")
            saved = self.root/"data/episodes"/result["episode_id"]
            wait_export(saved)
            _,data = read_aligned(saved)
            self.assertGreater(len(data), 10)
            self.assertTrue(all(len(r["sample"]["reference"]["q_target"]) == 3 for r in data))
            self.assertTrue(all(0 <= r["camera_age_ns"] <= 75_000_000 for r in data))
        finally:
            udp.close()
            publisher.close()
            context.term()
