import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from onboardmimic_lab.replay_console import ReplayConsole
from onboardmimic_lab.console import make_server
from onboardmimic_lab.reference_dataset import RawEpisode
from onboardmimic_lab.dataset import digest, write_json
from onboardmimic_lab.export_jobs import finish_export
from onboardmimic_lab.lerobot_dataset import verify
from test_sonic_native import fixtures, shifted
from test_collection import make_video


class ReplayConsoleTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.episode=self.root/'episodes'/'native-test'
        spec, sample=fixtures();raw=RawEpisode(self.episode,dict(controller_spec=spec,task='pick cup'))
        video=make_video(self.root/'input.ts');raw.video(video)
        from onboardmimic_lab.video_timing import MpegTsPtsExtractor
        for i,pts in enumerate(MpegTsPtsExtractor().feed(video)):
            s=shifted(sample,i);stamp=s['timestamp_monotonic_ns']
            raw.controller(s,stamp+2_000_000)
            raw.append('camera.jsonl',dict(sequence=i,pts_90khz=pts,monotonic_ns=stamp+1_000_000))
        raw.finish();finish_export(raw.root)
        self.replay=ReplayConsole(self.root);self.addCleanup(self.replay.close)

    def test_legacy_saved_episodes_are_not_offered(self):
        legacy=self.root/'episodes'/'old';legacy.mkdir()
        write_json(legacy/'manifest.json',dict(status='saved',controller_spec=dict(schema_version=2),counts={},format='onboardmimic_lab_multimodal_v2'))
        self.assertEqual([e['id'] for e in self.replay.status()['episodes']],['native-test'])
        with self.assertRaises(ValueError):self.replay.command(dict(action='inspect',episode='old'))

    def test_list_validate_and_reject_tampered_data(self):
        status=self.replay.status();self.assertEqual(status['episodes'][0]['frames'],19)
        self.assertFalse(status['real_supported']);self.assertFalse(status['simulation_configured'])
        result=self.replay.command(dict(action='inspect',episode='native-test'))
        self.assertEqual(result['summary']['frames'],19)
        with (self.root/verify(self.episode)['paths']['data.parquet']).open('ab') as f:f.write(b'corrupted')
        with self.assertRaisesRegex(ValueError,'integrity'):self.replay.command(dict(action='inspect',episode='native-test'))

    def test_no_arbitrary_paths_or_physical_commands(self):
        for name in ('../native-test','/etc/passwd','native-test/../../x'):
            with self.assertRaises(ValueError):self.replay.episode(name)
        (self.root/'episodes/linked').symlink_to(self.episode)
        with self.assertRaises(ValueError):self.replay.episode('linked')
        for action in ('start','stop','replay-real'):
            with self.assertRaises(ValueError):self.replay.command(dict(action=action,episode='native-test'))
        with self.assertRaisesRegex(ValueError,'not configured'):self.replay.command(dict(action='simulate',episode='native-test'))
        with self.assertRaises(ValueError):self.replay.command(dict(action='inspect',episode='native-test',segment=True))

    def test_preview_is_playable_and_bounded_to_saved_episode(self):
        preview=self.replay.preview('native-test')
        self.assertTrue(preview.is_file())
        self.assertIn(b'ftyp',preview.read_bytes()[:32])
        import subprocess
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
            '-show_entries','stream=codec_name,codec_tag_string,pix_fmt','-of','json',str(preview)]))['streams'][0]
        self.assertEqual(info['codec_name'],'h264');self.assertEqual(info['codec_tag_string'],'avc1')
        self.assertEqual(info['pix_fmt'],'yuv420p')
        subprocess.run(['ffmpeg','-v','error','-i',str(preview),'-f','null','-'],check=True,capture_output=True)
        self.assertEqual(preview,self.replay.preview('native-test'))
        with self.assertRaises(ValueError):self.replay.preview('../native-test')

    def test_discontinuity_requires_explicit_time_range(self):
        spec,sample=fixtures()
        raw=RawEpisode(self.root/'episodes'/'gapped',dict(controller_spec=spec,task='gap'))
        video=make_video(self.root/'gap.ts');raw.video(video)
        from onboardmimic_lab.video_timing import MpegTsPtsExtractor
        for i,pts in enumerate(MpegTsPtsExtractor().feed(video)):
            s=shifted(sample,i if i<10 else i+5);stamp=s['timestamp_monotonic_ns']
            raw.controller(s,stamp+2_000_000)
            raw.append('camera.jsonl',dict(sequence=i,pts_90khz=pts,monotonic_ns=stamp+1_000_000))
        raw.finish();finish_export(raw.root)
        result=self.replay.command(dict(action='inspect',episode='gapped'))
        self.assertFalse(result['verified']);self.assertEqual(len(result['segments']),2)
        self.assertLess(result['segments'][0]['end_s'],result['segments'][1]['start_s'])
        chosen=self.replay.command(dict(action='inspect',episode='gapped',segment=1))
        self.assertTrue(chosen['verified']);self.assertEqual(chosen['summary']['frames'],9)
        with self.assertRaises(ValueError):self.replay.command(dict(action='inspect',episode='gapped',segment=2))

    def test_stop_simulation_checks_job_identity(self):
        from unittest.mock import Mock,patch
        self.replay.job=dict(id='current',state='running')
        process=Mock(pid=12345);process.poll.return_value=None
        self.replay.process=process
        with patch('onboardmimic_lab.replay_console.os.killpg') as kill:
            with self.assertRaises(ValueError):self.replay.command(dict(action='simulate_stop',job_id='old'))
            kill.assert_not_called()
            result=self.replay.command(dict(action='simulate_stop',job_id='current'))
            self.assertEqual(result['state'],'stopping');kill.assert_called_once()
        self.replay.process=None

    def test_simulation_failure_is_reported_and_does_not_claim_success(self):
        config=self.root/'bad-config.json';config.write_text('{}')
        replay=ReplayConsole(self.root,config);self.addCleanup(replay.close)
        job=replay.command(dict(action='simulate',episode='native-test'))
        self.assertEqual(job['state'],'running')
        end=time.monotonic()+10
        while replay.status()['job']['state']=='running' and time.monotonic()<end:time.sleep(.02)
        status=replay.status()['job'];self.assertEqual(status['state'],'failed');self.assertTrue(status['error']);self.assertFalse(status['video'])

    def test_http_auth_and_bounded_result_routes(self):
        server=make_server(None,replay=self.replay);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)
        try:
            with urlopen(url+'/api/session') as r:token=json.load(r)['token']
            body=json.dumps(dict(action='inspect',episode='native-test')).encode()
            for origin in ('http://evil.invalid','http://127.0.0.1:8769'):
                with self.assertRaises(HTTPError) as e:urlopen(Request(url+'/api/replay',data=body,headers={'Origin':origin,'X-Control-Token':token}))
                self.assertEqual(e.exception.code,403)
            with urlopen(Request(url+'/api/replay',data=body,headers={'Origin':url,'X-Control-Token':token})) as r:self.assertTrue(json.load(r)['verified'])
            self.replay.job=dict(id='a'*32,state='completed');out=self.replay.output/('a'*32)/'simulation';out.mkdir(parents=True)
            (out/'replay.mp4').write_bytes(b'0123456789')
            with urlopen(Request(url+'/api/replay/file?id='+('a'*32)+'&name=replay.mp4',headers={'Range':'bytes=2-5'})) as r:
                self.assertEqual(r.status,206);self.assertEqual(r.read(),b'2345')
            with self.assertRaises(HTTPError):urlopen(url+'/api/replay/file?id=../../etc&name=passwd')
        finally:server.shutdown();server.server_close();thread.join()
