import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from onboardmimic_lab.contracts import fingerprint
from onboardmimic_lab.reference_dataset import RawEpisode
from onboardmimic_lab.lerobot_dataset import verify, records
from onboardmimic_lab.export_jobs import finish_export
from onboardmimic_lab.replay import prepare_plan
from onboardmimic_lab.replay_console import ReplayConsole
from onboardmimic_lab.tasks import Tasks, episode_numbers
from onboardmimic_lab.video_timing import MpegTsPtsExtractor
from test_collection import make_video
from test_sonic_native import fixtures, shifted


class OpenLambdaExportTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name in ('episodes','pending','discarded'):(self.root/name).mkdir()
        spec,base=fixtures()
        spec['loaded_parameters']['default_angles']=[0.]*29
        spec.update(physical_source=True,test_fixture=False)
        base['contract_sha256']=fingerprint(spec)
        gravity=next(k for k in spec['state'] if k.startswith('his_gravity_dir_'))
        base['state'][gravity]=[[0.,0.,-1.] for _ in range(10)]
        base['decoder_input']=[]
        for field in spec['decoder_input_layout']:
            name=field['name'];value=base['reference'].get(name,base['state'].get(name))
            base['decoder_input'].extend(value if name=='token_state' else [v for row in value for v in row])
        video=make_video(self.root/'input.ts')
        raw=RawEpisode(self.root/'episodes'/'fixture',dict(task='fixture',task_name='task',task_id='fixture-task',
            episode_index=0,collector_name='operator',controller_spec=spec))
        raw.video(video)
        for i,pts in enumerate(MpegTsPtsExtractor().feed(video)):
            s=shifted(base,i);raw.controller(s,s['published_monotonic_ns'])
            raw.append('camera.jsonl',dict(sequence=i,pts_90khz=pts,monotonic_ns=s['timestamp_monotonic_ns']))
        raw.finish();self.path=raw.root

    def test_three_files_and_replay_admission(self):
        from onboardmimic_lab.export_progress import ExportProgress
        seen=[]
        original_update=ExportProgress.update
        def observe(progress,stage,completed=None,total=None):
            seen.append((stage,completed,total))
            return original_update(progress,stage,completed,total)
        with patch.object(ExportProgress,'update',observe):
            finish_export(self.path)
        self.assertEqual(list(dict.fromkeys(row[0] for row in seen)),
                         ['verify_raw','video','verify_output','saving'])
        self.assertTrue(any(stage=='video' and total and done>0 for stage,done,total in seen))
        self.assertEqual([row for row in seen if row[0]=='verify_output'][-1],('verify_output',20,20))
        self.assertFalse(self.path.exists())
        self.assertTrue((self.root/'meta/info.json').is_file())
        m=verify(self.path);data=list(records(self.path))
        self.assertEqual(m['processing']['schema'], 1)
        self.assertGreater(m['processing']['stages']['source_decode_s'], 0)
        self.assertGreater(m['processing']['stages']['encode_s'], 0)
        self.assertGreaterEqual(m['processing']['elapsed_before_publish_s'],
                                m['processing']['stages']['decode_align_encode_s'])
        self.assertEqual(m['frames'],20)
        self.assertEqual((m['state_dim'],m['action_dim']),(32,64))
        self.assertFalse(any('history' in k or 'decoder' in k for k in data[0]))
        self.assertEqual(data[0]['action'],data[0]['action.motion_token'])
        plan=prepare_plan(self.path)
        from onboardmimic_lab.lerobot_replay import validate_plan as validate
        native=validate(plan)
        self.assertEqual(native['frames'][0]['sample']['reference']['token_state'],data[0]['action.motion_token'])
        self.assertEqual(native['frames'][0]['sample']['state']['body_q'],data[0]['observation.body_joint_position'])
        console=ReplayConsole(self.root)
        self.assertEqual(console.preview('fixture'),self.root/m['paths']['ego_view.mp4'])
        self.assertEqual(console.status()['episodes'][0]['collector_name'],'operator')
        self.assertEqual(episode_numbers(self.root)['fixture']['index'],0)
        self.assertTrue(Tasks(self.root).list())
        finish_export(self.path) # recovery is idempotent, no raw log required
        plan['frames'][1]['sample']['index']+=1
        with self.assertRaises(ValueError):validate(plan)

    def test_confirmed_consumption_sequence_survives_export_and_native_replay(self):
        from copy import deepcopy
        original=json.loads((self.path/'manifest.json').read_text())
        raw=RawEpisode(self.root/'episodes'/'proven',dict(original,id='proven'))
        raw.video((self.path/'camera.ts').read_bytes())
        for line in (self.path/'camera.jsonl').read_text().splitlines():raw.append('camera.jsonl',json.loads(line))
        prev=None
        for i,line in enumerate((self.path/'controller.jsonl').read_text().splitlines()):
            row=json.loads(line);s=deepcopy(row['sample']);s['index']+=int(i>=10)
            s['consumption']=dict(schema=1,sequence=i+1,segment=0,previous_inference_index=prev,
                reason='start' if i==0 else 'superseded_before_commit' if i==10 else 'adjacent',skipped_attempts=int(i==10))
            raw.controller(s,row['received_monotonic_ns']);prev=s['index']
        raw.finish();finish_export(raw.root)
        meta=verify(raw.root);self.assertEqual(len(meta['segments']),1)
        data=list(records(raw.root));self.assertEqual(sum(r['quality.superseded_attempts'] for r in data),1)
        self.assertEqual(data[10]['source.robot_index']-data[9]['source.robot_index'],2)
        self.assertEqual(data[10]['source.consumption_sequence']-data[9]['source.consumption_sequence'],1)
        from onboardmimic_lab.lerobot_replay import validate_plan as validate
        plan=prepare_plan(raw.root);validate(plan)
        self.assertEqual(plan['frames'][10]['sample']['inference_index'],data[10]['source.robot_index'])
        plan['frames'][10]['sample']['index']+=1
        with self.assertRaises(ValueError):validate(plan)

    def test_failed_publication_retains_raw(self):
        with patch('onboardmimic_lab.lerobot_dataset.verify_artifacts',side_effect=ValueError('verification failed')):
            with self.assertRaises(ValueError):finish_export(self.path)
        self.assertTrue((self.path/'controller.jsonl').is_file())
        self.assertTrue((self.path/'camera.ts').is_file())
        self.assertFalse((self.root/'meta/episodes.jsonl').exists())
        finish_export(self.path)
        target=self.root/verify(self.path)['paths']['data.parquet'];target.write_bytes(target.read_bytes()[:-10])
        with self.assertRaises(ValueError):prepare_plan(self.path)

    def test_cleanup_interruption_is_recoverable(self):
        with patch('onboardmimic_lab.lerobot_dataset.cleanup',side_effect=OSError('interrupted cleanup')):
            with self.assertRaises(OSError):finish_export(self.path)
        self.assertTrue((self.root/'meta/episodes.jsonl').is_file())
        self.assertTrue((self.path/'controller.jsonl').is_file())
        verify(self.path)
        finish_export(self.path)
        self.assertFalse(self.path.exists())
        self.assertTrue((self.root/'meta/info.json').is_file())

    def test_background_worker_survives_manifest_removal(self):
        import time
        from onboardmimic_lab.dataset_io import write_json
        from onboardmimic_lab.export_jobs import ExportJobs
        m=json.loads((self.path/'manifest.json').read_text())
        m.update(status='raw_saved',export=dict(state='queued'))
        write_json(self.path/'manifest.json',m)
        jobs=ExportJobs(self.root,lambda:False)
        try:
            end=time.monotonic()+15
            while time.monotonic()<end:
                status=jobs.status()
                if status and status[0]['state'] in ('complete','failed'):break
                time.sleep(.05)
            self.assertEqual(status[0]['state'],'complete',status)
            self.assertFalse((self.path/'manifest.json').exists())
            verify(self.path)
        finally:jobs.close()

    def test_multiple_episodes_use_standard_shared_metadata(self):
        import shutil
        from onboardmimic_lab.dataset_io import write_json
        second=self.path.parent/'second'
        shutil.copytree(self.path,second)
        m=json.loads((second/'manifest.json').read_text());m['episode_index']=1
        write_json(second/'manifest.json',m)
        finish_export(self.path);finish_export(second)
        info=json.loads((self.root/'meta/info.json').read_text())
        self.assertEqual(info['codebase_version'],'v2.1')
        self.assertEqual((info['total_episodes'],info['total_frames'],info['total_tasks']),(2,40,1))
        data=list(records(second))
        self.assertEqual([r['index'] for r in data],list(range(20,40)))
        self.assertEqual({r['episode_index'] for r in data},{1})
        self.assertEqual([r['frame_index'] for r in data],list(range(20)))
        self.assertEqual(len(ReplayConsole(self.root).status()['episodes']),2)

    def test_metadata_publication_failure_keeps_previous_dataset_loadable(self):
        import shutil
        from onboardmimic_lab import lerobot_storage
        second=self.path.parent/'second';shutil.copytree(self.path,second)
        finish_export(self.path)
        previous=(self.root/'meta/episodes.jsonl').read_bytes()
        real_write=lerobot_storage.write_metadata
        calls=0
        def fail_once(root, rows):
            nonlocal calls
            calls+=1
            if calls==1:raise OSError('metadata publication interrupted')
            return real_write(root,rows)
        with patch('onboardmimic_lab.lerobot_storage.write_metadata',side_effect=fail_once):
            with self.assertRaises(OSError):finish_export(second)
        self.assertEqual((self.root/'meta/episodes.jsonl').read_bytes(),previous)
        self.assertEqual(len(list((self.root/'data').rglob('*.parquet'))),1)
        self.assertTrue((second/'controller.jsonl').exists())
        finish_export(second)
        self.assertEqual(len(lerobot_storage.entries(self.root)),2)
