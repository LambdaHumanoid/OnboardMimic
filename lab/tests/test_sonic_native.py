from export_test_helpers import wait_export
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from onboardmimic_lab.contracts import validate_spec, validate_sample, fingerprint
from onboardmimic_lab.reference_dataset import RawEpisode
from onboardmimic_lab.dataset import export_episode,read_aligned,write_json,digest
from onboardmimic_lab.replay import prepare_plan,validate_plan
from onboardmimic_lab.collection import validate_config
from onboardmimic_lab.video_timing import MpegTsPtsExtractor
from test_collection import make_video

FIXTURE=Path(__file__).parent/'fixtures/sonic-native'


def fixtures(encoder=False):
    spec=json.loads((FIXTURE/'spec.json').read_text());sample=json.loads((FIXTURE/'sample.json').read_text())
    spec['loaded_parameters']['default_angles']=[0.]*29
    if encoder:
        # Synthetic values, matching the deployed encoder's 1762D input layout.
        layout=[('encoder_mode_4',4),('motion_joint_positions_10frame_step5',290),
            ('motion_joint_velocities_10frame_step5',290),('motion_root_z_position_10frame_step5',10),
            ('motion_root_z_position',1),('motion_anchor_orientation',6),
            ('motion_anchor_orientation_10frame_step5',60),('motion_joint_positions_lowerbody_10frame_step5',120),
            ('motion_joint_velocities_lowerbody_10frame_step5',120),('vr_3point_local_target',9),
            ('vr_3point_local_orn_target',12),('smpl_joints_10frame_step1',720),
            ('smpl_anchor_orientation_10frame_step1',60),('motion_joint_positions_wrists_10frame_step1',60)]
        offset=0;spec['encoder_layout']=[]
        for name,dimension in layout:
            spec['encoder_layout'].append(dict(name=name,dimension=dimension,offset=offset));offset+=dimension
        spec['loaded_parameters']['is_using_encoder']=True
        spec['diagnostic_tensors']['encoder_input']=dict(shape=[offset],dtype='float32',unit='mixed',
            frame='encoder_flat_input',semantic='exact_encoder_input')
        for name in ('encoder_loaded_trt','encoder_onnx_cache_validated'):
            spec['artifacts'][name]=fingerprint('TEST_FIXTURE_'+name)
        sample['encoder_input']=[0.]*offset
        sample['encoder_input_monotonic_ns']=sample['timestamp_monotonic_ns']-1_000_000
    sample['contract_sha256']=fingerprint(spec)
    return spec,sample


def shifted(original,index):
    s=deepcopy(original);delta=index*20_000_000;s['index']+=index;s['reference_id']+=':'+str(index)
    for key in ('timestamp_monotonic_ns','timestamp_realtime_ns','inference_finished_monotonic_ns','published_monotonic_ns'):
        s[key]+=delta
    if s['encoder_input_monotonic_ns'] is not None:s['encoder_input_monotonic_ns']+=delta
    s['output_commit']['timestamp_monotonic_ns']+=delta;s['output_commit']['sequence']+=index
    for name in s['state_history_ns']:
        s['state_history_ns'][name]=[t+delta for t in s['state_history_ns'][name]]
        s['state_history_indices'][name]=[i+index for i in s['state_history_indices'][name]]
        for k,v in s['state_history_sources'][name].items():s['state_history_sources'][name][k]=[t+delta for t in v]
    return s


class NativeTest(unittest.TestCase):
    def test_consumption_continuity_keeps_real_gaps_and_timeouts(self):
        from onboardmimic_lab.contracts import next_segment
        spec,base=fixtures();a=shifted(base,0);b=shifted(base,2)
        a['consumption']=dict(schema=1,sequence=1,segment=0,reason='start',previous_inference_index=None,skipped_attempts=0)
        b['consumption']=dict(schema=1,sequence=2,segment=0,reason='superseded_before_commit',previous_inference_index=a['index'],skipped_attempts=1)
        self.assertEqual(next_segment(a,b,0,spec),0)
        for key,value in [('sequence',3),('segment',1),('reason','unresolved'),('skipped_attempts',0)]:
            bad=deepcopy(b);bad['consumption'][key]=value
            self.assertEqual(next_segment(a,bad,0,spec),1)
        late=shifted(base,4);late['consumption']=dict(b['consumption'],skipped_attempts=3)
        self.assertEqual(next_segment(a,late,0,spec),1)

    def test_fixture_and_numeric_timing_rejections(self):
        spec,sample=fixtures();validate_spec(spec);validate_sample(sample,spec)
        self.assertEqual(len(sample['decoder_input']),994)
        field=next(iter(spec['state']))
        bads=[]
        s=deepcopy(sample);s['decoder_input'][0]+=1;bads.append(s)
        s=deepcopy(sample);s['state_history_ns'][field][1]=s['state_history_ns'][field][0];bads.append(s)
        s=deepcopy(sample);s['state_history_ns'][field][-1]=s['timestamp_monotonic_ns']+1;bads.append(s)
        s=deepcopy(sample);s['state_history_sources'][field]['low_state_received_ns'][0]=s['timestamp_monotonic_ns'];bads.append(s)
        s=deepcopy(sample);s['output_commit']['timestamp_monotonic_ns']=1;bads.append(s)
        s=deepcopy(sample);s['output_commit']['boundary']='motor_executed';bads.append(s)
        s=deepcopy(sample);s['raw_policy_action'][0]=float('nan');bads.append(s)
        for s in bads:
            with self.assertRaises(ValueError):validate_sample(s,spec)
        old=deepcopy(spec);old['schema_version']=1
        with self.assertRaises((ValueError,KeyError)):validate_spec(old)
        config=dict(capture_adapter='reference-v1',controller_spec=spec,root='tmp',same_host=True,telemetry_endpoint='tcp://127.0.0.1:16691')
        with self.assertRaises(ValueError):validate_config(config)
        config['capture_adapter']='sonic-native-v2';validate_config(config)

    def test_record_save_plan_uses_input_time_not_commit_time(self):
        spec,template=fixtures()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);video=make_video(root/'input.ts');raw=RawEpisode(root/'episodes'/'episode',dict(task='native fixture',controller_spec=spec))
            raw.video(video)
            for i,pts in enumerate(MpegTsPtsExtractor().feed(video)):
                s=shifted(template,i);raw.controller(s,s['published_monotonic_ns'])
                # Image after input but before output must NOT be used for that tick.
                raw.append('camera.jsonl',dict(sequence=i,pts_90khz=pts,monotonic_ns=s['timestamp_monotonic_ns']+1_000_000))
            raw.finish()
            from onboardmimic_lab.export_jobs import finish_export
            finish_export(raw.root)
            metadata,data=read_aligned(raw.root)
            self.assertEqual(len(data),19);self.assertEqual(data[0]['source.camera_sequence'],0)
            self.assertEqual(data[0]['source.robot_index'],2)
            plan=prepare_plan(raw.root);self.assertEqual(plan['schema_version'],2);self.assertEqual(len(plan['frames']),19)
            bad=deepcopy(plan);bad['schema_version']=1
            with self.assertRaises(ValueError):validate_plan(bad)

    def test_mode_switches_through_real_collector(self):
        import socket,time,zmq
        from onboardmimic_lab.collection import Collector
        spec,base=fixtures(encoder=True)
        spec.update(recording_policy='actual_decoder_consumption',record_modes=['planner','pose','replay'])
        base['contract_sha256']=fingerprint(spec)
        current=dict(mode='pose',switching=False)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);video=make_video(root/'live.ts',frames=100)
            groups=[];group=bytearray()
            for offset in range(0,len(video),188):
                packet=video[offset:offset+188]
                if MpegTsPtsExtractor._packet_pts(packet) is not None and group:groups.append(bytes(group));group.clear()
                group.extend(packet)
            if group:groups.append(bytes(group))
            context=zmq.Context();pub=context.socket(zmq.PUB);pub.setsockopt(zmq.LINGER,0)
            port=pub.bind_to_random_port('tcp://127.0.0.1')
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as reserve:
                reserve.bind(('127.0.0.1',0));camera=reserve.getsockname()[1]
            config=validate_config(dict(capture_adapter='sonic-native-v2',controller_spec=spec,root=str(root/'data'),
                same_host=True,telemetry_endpoint='tcp://127.0.0.1:'+str(port),camera_port=camera,max_duration_s=1))
            observations=[];sent={}
            def runtime():
                status=dict(state='active',input_mode=current['mode'],mode_switch_pending=current['switching'],pose_input_ready=False,lab=dict(record_ready=True,reason='',contract_sha256=fingerprint(spec),session_id=base['session_id']))
                if len(observations)==2:
                    status['lab'].update(record_ready=False,reason='native_consumption_stale_or_warming')
                observations.append(status)
                return status
            collector=Collector(config,runtime);udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            try:
                collector.command(dict(action='start',task='synthetic native wire'))
                for i,group in enumerate(groups):
                    # Shift all source timestamps by the same offset; retain irregular intervals.
                    s=shifted(base,i);delta=time.monotonic_ns()-25_000_000-s['timestamp_monotonic_ns']
                    for key in ('timestamp_monotonic_ns','inference_finished_monotonic_ns','published_monotonic_ns'):s[key]+=delta
                    s['encoder_input_monotonic_ns']+=delta
                    s['timestamp_realtime_ns']=time.time_ns()
                    s['output_commit']['timestamp_monotonic_ns']+=delta
                    for name in s['state_history_ns']:
                        s['state_history_ns'][name]=[v+delta for v in s['state_history_ns'][name]]
                        for key in s['state_history_sources'][name]:s['state_history_sources'][name][key]=[v+delta for v in s['state_history_sources'][name][key]]
                    mode=(2,1,2,3)[(i//10)%4]
                    current.update(mode={1:'planner',2:'pose',3:'replay'}[mode],switching=i%10<5)
                    s.update(native_mode=mode,token_source='encoder' if s['encoder_input'] is not None else 'external',replay=None)
                    if mode==3:
                        s.update(token_source='replay',encoder_input=None,encoder_input_monotonic_ns=None,
                                 replay=dict(run=1,frame=i,scheduled_frame=True))
                    token=[i/128.]*64
                    s['reference']['token_state']=token
                    layout=next(f for f in spec['decoder_input_layout'] if f['name']=='token_state')
                    s['decoder_input'][layout['offset']:layout['offset']+64]=token
                    sent[s['index']]=deepcopy(s)
                    pub.send_multipart([b'oml/controller/v2-draft',json.dumps(s).encode()])
                    for offset in range(0,len(group),1316):udp.sendto(group[offset:offset+1316],('127.0.0.1',camera))
                    status=collector.status()
                    if status['state'] in ('review','failed'):break
                    time.sleep(.02)
                self.assertEqual(status['state'],'recording',status)
                self.assertNotIn('max_duration_s',config)
                collector.command(dict(action='stop'))
                end=time.monotonic()+3
                while time.monotonic()<end and collector.status()['state']=='stopping':time.sleep(.01)
                status=collector.status()
                self.assertEqual(status['state'],'review',status)
                self.assertTrue(any(not s['lab']['record_ready'] for s in observations))
                self.assertGreater(status['counts']['controller.jsonl'],10)
                # Inspect raw provenance before successful compact export removes diagnostics.
                raw=Path(config['root'])/'pending'/status['episode_id']
                captured=[json.loads(line)['sample'] for line in (raw/'controller.jsonl').read_text().splitlines()]
                self.assertEqual({s['native_mode'] for s in captured},{1,2,3})
                self.assertEqual(len({s['session_id'] for s in captured}),1)
                self.assertTrue(all(len(s['encoder_input'])==1762 for s in captured if s['native_mode']!=3))
                collector.command(dict(action='save'))
                end=time.monotonic()+10
                while time.monotonic()<end and collector.status()['state']=='saving':time.sleep(.01)
                self.assertEqual(collector.status()['state'],'saved',collector.status())
                wait_export(Path(config['root'])/'episodes'/status['episode_id'])
                meta,data=read_aligned(Path(config['root'])/'episodes'/status['episode_id'])
                self.assertGreater(len(data),10)
                self.assertTrue(all(len(v['action.motion_token'])==64 for v in data))
                for row in data:
                    self.assertEqual(row['action.motion_token'],sent[row['source.robot_index']]['reference']['token_state'])
                # Real socket timing may introduce genuine gaps. Validate every
                # replayable segment without silently joining across those gaps.
                checked=0
                for segment in meta['segments']:
                    selected=data[segment['start_frame']:segment['end_frame_exclusive']]
                    if len(selected)<2:continue
                    plan=prepare_plan(Path(config['root'])/'episodes'/status['episode_id'],segment['segment_index'])
                    self.assertEqual(plan['sample_profile'],'lerobot_body_v21')
                    self.assertEqual(len(plan['frames']),len(selected))
                    for frame,row in zip(plan['frames'],selected):
                        self.assertEqual(frame['sample']['reference']['token_state'],row['action.motion_token'])
                        self.assertEqual(frame['sample']['state']['body_q'],row['observation.body_joint_position'])
                    checked+=1
                self.assertGreater(checked,0)
            finally:collector.close();udp.close();pub.close();context.term()

    def test_pose_encoder_switches_preserve_continuous_tokens(self):
        """Fixed source clocks isolate mode transitions from host scheduling jitter."""
        spec,base=fixtures(encoder=True)
        spec.update(recording_policy='actual_decoder_consumption',record_modes=['planner','pose','replay'])
        base['contract_sha256']=fingerprint(spec)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);video=make_video(root/'input.ts')
            raw=RawEpisode(root/'episodes'/'pose',dict(task='synthetic Pose',controller_spec=spec))
            raw.video(video);tokens=[]
            for i,pts in enumerate(MpegTsPtsExtractor().feed(video)):
                s=shifted(base,i)
                s.update(native_mode=2 if i//5%2==0 else 1,token_source='encoder',replay=None)
                token=[i/128.]*64;tokens.append(token)
                s['reference']['token_state']=token
                field=next(f for f in spec['decoder_input_layout'] if f['name']=='token_state')
                s['decoder_input'][field['offset']:field['offset']+64]=token
                s['encoder_input'][922:1642]=[i/128.]*720
                raw.controller(s,s['published_monotonic_ns'])
                raw.append('camera.jsonl',dict(sequence=i,pts_90khz=pts,monotonic_ns=s['timestamp_monotonic_ns']))
            raw.finish()
            from onboardmimic_lab.export_jobs import finish_export
            finish_export(raw.root)
            meta,data=read_aligned(raw.root)
            self.assertEqual(len(meta['segments']),1)
            self.assertEqual(meta['rejected_frames'],0)
            self.assertEqual([r['action.motion_token'] for r in data],tokens)
            plan=prepare_plan(raw.root)
            self.assertEqual([f['sample']['reference']['token_state'] for f in plan['frames']],tokens)

    def test_mode_neutral_readiness_and_provenance(self):
        from onboardmimic_lab.adapters.reference_capture import ready
        spec,sample=fixtures()
        spec.update(recording_policy='actual_decoder_consumption',record_modes=['planner','pose','replay'])
        sample.update(contract_sha256=fingerprint(spec),native_mode=1,
                      token_source='encoder' if sample['encoder_input'] is not None else 'external',replay=None)
        validate_spec(spec);validate_sample(sample,spec)
        for mode in ('planner','pose','replay'):
            status=dict(state='active',input_mode=mode,mode_switch_pending=True,pose_input_ready=False,
                        lab=dict(record_ready=True,contract_sha256=fingerprint(spec),session_id='session'))
            self.assertTrue(ready(status,spec))
            status['lab']['record_ready']=False;self.assertFalse(ready(status,spec))
        for key,value in [('native_mode','pose'),('token_source','unknown'),('replay',{'run':1})]:
            bad=deepcopy(sample);bad[key]=value
            with self.assertRaises(ValueError):validate_sample(bad,spec)
        sample.update(native_mode=3,token_source='replay',replay=dict(run=1,frame=0,scheduled_frame=True),
                      encoder_input=None,encoder_input_monotonic_ns=None)
        validate_sample(sample,spec)
        bad=deepcopy(sample);bad['encoder_input_monotonic_ns']=sample['timestamp_monotonic_ns']
        with self.assertRaises(ValueError):validate_sample(bad,spec)

    def test_deferred_exported_spec_is_loaded_at_record_start(self):
        from onboardmimic_lab.adapters.capture import load_spec
        from onboardmimic_lab.adapters.reference_capture import ReferenceCapture
        spec,_=fixtures()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'live-spec.json'
            config=validate_config(dict(capture_adapter='sonic-native-v2',root=d,controller_spec_file=str(path),
                same_host=True,telemetry_endpoint='tcp://127.0.0.1:16691'))
            with self.assertRaisesRegex(ValueError,'Waiting for OM'):load_spec(config)
            path.write_text(json.dumps(spec))
            with self.assertRaisesRegex(ValueError,'not a fixture'):load_spec(config)
            spec.update(test_fixture=False,physical_source=True)
            path.write_text(json.dumps(spec))
            status=dict(state='active',input_mode='pose',lab=dict(record_ready=True,contract_sha256=fingerprint(spec),session_id='test'))
            metadata=ReferenceCapture().metadata(config,status)
            spec['artifacts']['native_executable']='0'*64;path.write_text(json.dumps(spec))
            self.assertNotEqual(metadata['controller_spec'],load_spec(config))
            with self.assertRaisesRegex(ValueError,'readiness'):ReferenceCapture().metadata(config,status)
