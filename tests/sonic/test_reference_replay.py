"""Synthetic provenance and fake owned processes only; no hardware."""
import json
import threading
import unittest
import urllib.request
import urllib.error
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tests.sonic.test_native_consumption import fixture
from tests.fixtures import NativeLifecycle
from tests.runtime.test_native_lifecycle import Process
from onboardmimic.backends.sonic.native.consumption import NativeJoin
from onboardmimic.backends.sonic.native.replay_plan import validate, compatible
from onboardmimic.backends.sonic.native.replay import Replay
from onboardmimic.core.execution.software import fingerprint
from onboardmimic.core.gateway import make_server


def synthetic_source_plan(modes=(2,2,2), using_encoder=False, offsets=(0,20000000,40000000)):
    n,b,a,m,c,now=fixture()
    # Explicit unit substitution, never exported as physical evidence.
    n['test_fixture']=b['test_fixture']=False;b['physical']=True
    n['parameters']['default_angles']=[0.]*29
    if using_encoder:
        n['parameters']['is_using_encoder']=True
        n['encoder_layout']=[dict(name='encoder_mode_4',offset=0,dimension=4)]
        n['artifacts'].update(encoder_loaded_trt='b'*64,encoder_onnx_cache_validated='c'*64)
        a.update(encoder_input=[0.,0.,1.,0.],encoder_at_ns=979000000)
    a['input'][94:384]=[0.]*290;a['input'][-30:]=[0.,0.,-1.]*10
    join=NativeJoin(clock_ns=lambda:now)
    def emit(source,event):return join.ingest(source,dict(event,native_session=source,published_monotonic_ns=now))
    emit('native',dict(kind='manifest',manifest=n));emit('bridge',dict(kind='manifest',manifest=b));rows=[]
    for i in range(3):
        aa,mm,cc=deepcopy(a),deepcopy(m),deepcopy(c);delta=offsets[i];now=1000000000+delta
        aa['index']+=i;mm['index']+=i;mm['seq']+=i;cc['seq']+=i
        aa['mode']=modes[i]
        if using_encoder:aa['encoder_at_ns']+=delta
        if aa['mode']==3:aa['replay']=dict(run=1,frame=i,scheduled_frame=True)
        for obj,keys in [(aa,['at_ns','realtime_ns','finished_ns']),(mm,['send_ns']),(cc,['at_ns','send_ns'])]:
            for key in keys:obj[key]+=delta
        for h in aa['histories'][1:]:
            for key in ('ns','low_state_received_ns','torso_imu_received_ns'):h[key]=[v+delta for v in h[key]]
            h['index']=[v+i for v in h['index']]
        emit('native',aa);emit('native',mm);sample=emit('bridge',cc);assert sample
        rows.append(dict(offset_ns=delta,sample=sample))
    return dict(schema='onboardmimic.lab.reference-plan',schema_version=2,episode='UNIT_TEST_ONLY',segment_index=0,
        source_sha256='a'*64,controller_spec=join.spec,contract_sha256=fingerprint(join.spec),frames=rows)


def compact_plan(source):
    plan=deepcopy(source)
    plan.update(sample_profile='lerobot_body_v21',reference_evidence='consumed_at_capture')
    spec=plan['controller_spec']
    for row in plan['frames']:
        sample=row['sample']
        latest=lambda prefix:sample['state'][next(k for k in spec['state'] if k.startswith(prefix))][-1]
        q=[0.]*29;dq=[0.]*29
        for i,hw in enumerate(spec['loaded_parameters']['mujoco_to_isaaclab']):
            q[hw]=latest('his_body_joint_positions_')[i]+spec['loaded_parameters']['default_angles'][hw]
            dq[hw]=latest('his_body_joint_velocities_')[i]
        row['sample']={k:sample[k] for k in ('index','timestamp_monotonic_ns','timestamp_realtime_ns','reference')}
        row['sample']['state']=dict(body_q=q,body_dq=dq,gravity=latest('his_gravity_dir_'))
    return plan


def synthetic_plan(*args,**kwargs):
    return compact_plan(synthetic_source_plan(*args,**kwargs))


class ReplayPlanTests(unittest.TestCase):
    def test_long_plan_keeps_all_frames_and_source_times(self):
        plan=synthetic_plan();first=plan['frames'][0]['sample'];frames=[]
        for i in range(6002):
            sample=deepcopy(first)
            sample.update(index=first['index']+i,
                timestamp_monotonic_ns=first['timestamp_monotonic_ns']+i*20000000,
                timestamp_realtime_ns=first['timestamp_realtime_ns']+i*20000000)
            frames.append(dict(offset_ns=i*20000000,sample=sample))
        plan['frames']=frames
        result=validate(plan)
        self.assertEqual(len(result['native']['frames']),6002)
        self.assertEqual(result['timing']['scheduled_duration_ns'],120040000000)
        self.assertEqual(result['source_indices'][-1],first['index']+6001)

    def test_legacy_plan_is_rejected(self):
        with self.assertRaises(ValueError):validate(synthetic_source_plan())
        plan=synthetic_plan()
        self.assertEqual(len(validate(plan)['native']['frames']),3)
        plan['sample_profile']='unknown'
        with self.assertRaises(ValueError):validate(plan)

    def test_nonuniform_source_uses_sequential_execution_clock(self):
        plan=synthetic_plan(offsets=(0,42000000,45000000))
        before=deepcopy(plan)
        result=validate(plan)
        self.assertEqual(result['timing'],dict(mode='sequential_50hz',period_ns=20000000,
            source_duration_ns=65000000,scheduled_duration_ns=60000000))
        self.assertEqual([f['offset_ns'] for f in result['native']['frames']],[0,42000000,45000000])
        self.assertEqual(plan,before)

    def test_isolated_loader_preserves_validation_and_plan_identity(self):
        from onboardmimic.backends.sonic.native.replay_loader import PlanLoader
        loader=PlanLoader();plan=synthetic_plan()
        self.assertEqual(loader.decode(json.dumps(dict(plan=plan)).encode()),validate(plan))
        plan['frames'][1]['offset_ns']+=1
        with self.assertRaisesRegex(ValueError,'offset'):
            loader.decode(json.dumps(dict(plan=plan)).encode())
        with self.assertRaises(ValueError):loader.decode(b'{')
        loader.lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'Another replay'):
                loader.decode(b'{}')
        finally:loader.lock.release()

    def test_mixed_modes_keep_consumed_tokens_without_diagnostics(self):
        source=synthetic_source_plan((1,2,3),using_encoder=True)
        plan=compact_plan(source)
        actual=validate(plan)
        self.assertEqual([f['token'] for f in actual['native']['frames']],
                         [f['sample']['reference']['token_state'] for f in source['frames']])
        self.assertTrue(all('decoder_input' not in f['sample'] for f in plan['frames']))

    def test_accepts_v2_sends_only_tokens(self):
        p=validate(synthetic_plan());self.assertEqual(set(p['native']),{'entry_protocol','period_ns','artifacts','frames','start_q','start_gravity'})
        self.assertEqual(set(p['native']['frames'][0]),{'offset_ns','token'});self.assertEqual(p['source_indices'],[1,2,3])

    def test_format_fixture_and_digest(self):
        for key,value in [('schema_version',1),('contract_sha256','0'*64)]:
            p=synthetic_plan();p[key]=value
            with self.assertRaises(ValueError):validate(p)
        p=synthetic_plan();p['controller_spec']['test_fixture']=True
        with self.assertRaises(ValueError):validate(p)

    def test_tamper_and_discontinuities(self):
        mutations=[lambda f:f['sample']['reference']['token_state'].__setitem__(0,float('nan')),
            lambda f:f['sample']['state']['body_q'].pop(),
            lambda f:f['sample']['state'].__setitem__('gravity',[0.,0.,0.]),
            lambda f:f.__setitem__('offset_ns',1),lambda f:f['sample'].__setitem__('index',4),
            lambda f:f['sample'].__setitem__('timestamp_realtime_ns',0)]
        for mutate in mutations:
            p=synthetic_plan();mutate(p['frames'][1])
            with self.assertRaises(ValueError):validate(p)

    def test_binary_provenance_vs_model_identity(self):
        s=synthetic_plan()['controller_spec'];live=deepcopy(s);live['artifacts']['native_executable']='c'*64;compatible(s,live)
        live['artifacts']['decoder_loaded_trt']='d'*64
        with self.assertRaises(ValueError):compatible(s,live)
        live=deepcopy(s);live['loaded_parameters']['default_angles'][0]=1
        with self.assertRaises(ValueError):compatible(s,live)


class ReplayLifecycleTests(unittest.TestCase):
    def setUp(self):
        saver=patch('onboardmimic.backends.sonic.native.incident.save_incident',return_value='replay-test-incident')
        self.save_incident=saver.start();self.addCleanup(saver.stop)
        self.now=100.;self.proc=Process();self.g=NativeLifecycle([0.]*29,lambda:self.proc,lambda:'',lambda:None,physical=True,clock=lambda:self.now)
        g=self.g;g.process=self.proc;g.phase='active';g.still_since=99.;g.policy_feedback=(100.,dict(mode='planner',playing=True))
        g.ingest('rt/lowstate',dict(motor_state=[dict(q=0.,dq=0.,motorstate=0)]*29,imu_state={'quaternion':[1.,0.,0.,0.]}))
        g.ingest('rt/secondary_imu',{'quaternion':[1.,0.,0.,0.]})
        source=synthetic_source_plan(using_encoder=True);self.live_samples=[f['sample'] for f in source['frames']]
        self.plan=compact_plan(source);spec=self.plan['controller_spec'];self.join=SimpleNamespace(spec=spec,last=None)
        g.native_consumption=SimpleNamespace(lock=threading.RLock(),join=self.join,error=None,stop=threading.Event())
        self.p=dict(configured=True,fresh=True,armed=True,fenced=False,estopped=False,takeover_requested=False,
                    error='',wbc_pid=self.proc.pid,bridge_pid=888,generation=9,stream=111,phase=0)
        self.p['recording_identity']=dict(wbc_pid=self.proc.pid,bridge_pid=888,generation=9,
            native_executable=spec['artifacts']['native_executable'],bridge_executable=spec['artifacts']['bridge_executable'])
        g.protection_executor=Mock(snapshot=lambda:deepcopy(self.p))
        self.client=Mock();self.client.request.return_value=dict(ok=True,entry_protocol=3,state='running',reason='')
        self.r=Replay(g,'/UNIT_TEST_ONLY',client_factory=lambda *a:self.client,clock=lambda:self.now,start_worker=False);g.reference_replay=self.r

    def start(self):self.r.load(self.plan);self.r.start('test');self.r.step()

    def test_admission_applies_received_feedback_without_waiting_for_control_tick(self):
        from onboardmimic.backends.sonic.feedback import FeedbackInbox
        self.start()
        self.g.feedback_inbox=FeedbackInbox(self.g.robot.state_topics)
        self.g.require_continuity=True
        self.g.robot.health.status=dict(source='primary',at=100.,session='test',generation=1)
        self.g.robot.health.changed_at=99.
        old=deepcopy(self.g.latest)
        self.now+=.060
        self.assertFalse(self.g.feedback_ready())
        for topic, (_, value) in old.items():
            self.g.feedback_inbox.put(topic,value,self.now-.002)
        self.r._admit()
        self.assertTrue(self.g.feedback_ready())
        self.assertAlmostEqual(self.g.latest['rt/lowstate'][0],self.now-.002)
        self.assertAlmostEqual(self.g.feedback_timing['rt/lowstate']['inbox_wait_ms'],2.)
        self.assertEqual(self.proc.kills,0)
        # An old inbox item must retain its real reception time and still fail.
        self.now+=.060
        self.g.feedback_inbox.put('rt/lowstate',old['rt/lowstate'][1],self.now-.055)
        self.r.step()
        self.assertEqual(self.r.reason,'runtime_admission_lost')
        self.assertFalse(self.r.admission_failure['feedback_ready'])
        self.assertIn('topics',self.r.admission_failure['feedback_receiver'])
        self.assertEqual(self.proc.kills,1)

    def test_inbox_does_not_bypass_joint_fault_or_mode_admission(self):
        from onboardmimic.backends.sonic.feedback import FeedbackInbox
        self.start()
        self.g.feedback_inbox=FeedbackInbox(self.g.robot.state_topics)
        body=deepcopy(self.g.latest['rt/lowstate'][1])
        body['motor_state'][0]['motorstate']=1
        self.g.feedback_inbox.put('rt/lowstate',body,self.now)
        self.g.mode_pending=True
        self.r.step()
        self.assertEqual(self.r.reason,'runtime_admission_lost')
        self.assertTrue(self.r.admission_failure['mode_pending'])
        self.assertFalse(self.r.admission_failure['robot_safe'])

    def test_admission_does_not_copy_large_live_sample(self):
        self.start()
        class ExpensiveSample:
            def __deepcopy__(self,memo):raise AssertionError('sample copied during admission')
        self.join.last=ExpensiveSample()
        self.r._admit()

    def test_admission_failure_captures_feedback_age_before_shutdown(self):
        self.start()
        self.now+=.06
        self.g.feedback_ready=lambda:False
        self.r.step()
        data=self.save_incident.call_args.args[0]['admission']
        self.assertFalse(data['feedback_ready'])
        self.assertTrue(data['robot_safe'])
        self.assertFalse(data['mode_pending'])
        self.assertAlmostEqual(data['topic_age_ms']['rt/lowstate'],60.)
        self.assertIn('rt/lowstate',data['feedback_timing'])
        self.assertEqual(self.r.reason,'runtime_admission_lost')
        self.assertEqual(self.proc.kills,1)
        self.g.latest.clear()
        self.assertIn('rt/lowstate',data['topic_age_ms'])

    def test_admission_failure_distinguishes_mode_and_joint_fault(self):
        self.start()
        self.g.mode_pending=True
        self.g.latest['rt/lowstate'][1]['motor_state'][0]['motorstate']=1
        self.r.step()
        data=self.save_incident.call_args.args[0]['admission']
        self.assertTrue(data['mode_pending'])
        self.assertFalse(data['robot_safe'])
        self.assertTrue(data['telemetry']['faults'])

    def test_timeout_snapshots_before_halt_and_failure_does_not_block_protection(self):
        self.start()
        self.g.native_consumption.diagnostics={'native_0':{'index':42,'timing':{'thread_cpu_ns':123}}}
        self.save_incident.side_effect=lambda metadata: self.assertEqual(self.proc.kills,0) or 'incident-42'
        self.r.stop('consumption_timeout')
        data=self.save_incident.call_args.args[0]
        self.assertEqual(data['evidence']['stages']['native_0']['index'],42)
        self.assertEqual(self.r.status()['diagnostic_id'],'incident-42')
        self.assertEqual(self.proc.kills,1)

    def test_snapshot_error_still_exits(self):
        self.start();self.save_incident.side_effect=OSError('disk failure')
        with self.assertLogs('runtime_audit',level='WARNING'):
            self.r.stop('consumption_timeout')
        self.assertEqual(self.proc.kills,1)
        self.assertEqual(self.r.reason,'consumption_timeout')

    def test_load_no_motion_sent_not_consumed(self):
        self.r.load(self.plan);self.assertEqual(self.proc.kills,0);self.client.request.assert_not_called()
        self.r.start('test');self.assertEqual(self.g.input_mode,'replay');self.r.step()
        self.assertEqual(self.r.state,'starting');self.assertIsNone(self.r.frame)
        self.assertEqual(self.client.request.call_args_list[0].args,('load',))
        self.assertNotIn('state_history',json.dumps(self.client.request.call_args_list[0].kwargs))

    def test_authority_rejection(self):
        self.r.load(self.plan);self.p['armed']=False
        with self.assertRaises(ValueError):self.r.start('test')

    def test_old_native_cannot_start_without_entry_confirmation(self):
        self.client.request.return_value=dict(ok=True,state='loaded')
        self.r.load(self.plan);self.r.start('test')
        with self.assertRaisesRegex(ValueError,'transition protocol'):self.r.step()
        self.assertEqual([call.args[0] for call in self.client.request.call_args_list],['load'])

    def test_start_mismatch_enters_transition_without_advancing_recording(self):
        self.r.load(self.plan)
        self.g.latest['rt/lowstate'][1]['motor_state']=[dict(q=.2,dq=0.,motorstate=0)]*29
        self.r.start('test');self.r.step()
        self.assertEqual(self.r.state,'starting');self.assertIsNone(self.r.frame)
        self.assertEqual(self.proc.kills,0)
        self.client.request.return_value=dict(ok=True,state='starting',phase='aligning',reason='')
        self.r.step();self.assertEqual(self.r.status()['entry_phase'],'aligning')
        self.assertIsNone(self.r.status()['source_frame_position'])
        self.client.request.return_value=dict(ok=True,state='stopping',reason='start_pose_transition_timeout')
        self.r.step();self.assertEqual(self.r.reason,'start_pose_transition_timeout')
        self.g.protection_executor.cancel.assert_not_called()

    def test_native_watchdog_protected_exit_and_delayed_ack(self):
        self.start();self.client.request.return_value=dict(state='stopping',reason='lease_expired');self.r.step();self.assertEqual(self.r.reason,'lease_expired');self.assertEqual(self.proc.kills,1)
        self.g.protection_executor.cancel.assert_not_called();self.assertFalse(self.r.status()['stop_ack'])
        self.p.update(fenced=True,phase=2);self.r.step();self.assertTrue(self.r.status()['stop_ack']);self.assertEqual(self.r.state,'failed')

    def test_native_schedule_diagnostics_survive_protected_stop(self):
        self.start()
        schedule=dict(tick_gap_ns=60000001,lateness_ns=30000001,candidate_frame=368,
            recent=[dict(callback_gap_ns=60000001,thread_cpu_delta_ns=9000000,
                         sched_sample_ns=123456,sched_wait_total_ns=12000000)])
        self.client.request.return_value=dict(state='stopping',reason='schedule_overrun',phase='playing',schedule=schedule)
        with self.assertLogs('runtime_audit',level='WARNING') as logs:
            self.r.step()
        event=json.loads(logs.records[-1].getMessage())
        self.assertEqual(event['event'],'reference_replay_stop')
        self.assertEqual(json.loads(event['detail'])['schedule'],schedule)
        self.assertEqual(self.r.reason,'schedule_overrun')
        self.assertEqual(json.loads(self.r.status()['detail']),dict(phase='playing',schedule=schedule))
        self.assertEqual(self.proc.kills,1)
        self.g.protection_executor.cancel.assert_not_called()

    def test_stop_terminal_generation(self):
        self.start();self.r.stop();self.p.update(fenced=True,phase=2,generation=10);self.r.step();self.assertFalse(self.r.status()['stop_ack'])
        self.p['generation']=9;self.r.step();self.assertEqual(self.r.state,'stopped')

    def test_consumed_progress_completion(self):
        self.start();s=deepcopy(self.live_samples[1]);s['replay']=dict(run=self.r.run,frame=0,scheduled_frame=True)
        s['timestamp_monotonic_ns']=int(self.now*1e9);self.join.last=s;self.r.step();s['replay']['frame']=1;self.r.step();self.assertEqual(self.r.status()['source_frame_index'],2)
        s['replay']['frame']=2;self.r.step()
        self.assertTrue(self.r.returning);self.assertEqual(self.proc.kills,0)
        self.client.request.return_value=dict(ok=True,state='running',phase='settling_planner',return_ready_ns=int(self.now*1e9))
        self.r.step();self.assertNotEqual(self.r.state,'completed') # replay token is not Planner evidence
        planner=deepcopy(s);planner.update(native_mode=0,token_source='encoder',replay=None)
        planner['output_commit']['sequence']+=1;self.join.last=planner
        def reply(action,**kw):
            if action=='release':return dict(ok=True,state='completed')
            return dict(ok=True,state='running',phase='settling_planner',return_ready_ns=int(self.now*1e9))
        self.client.request.side_effect=reply
        # Production runtime receives only these IPC fields. Full synthetic
        # samples previously hid the missing Planner identity in this path.
        from onboardmimic.backends.sonic.native.process_receiver import EVIDENCE_KEYS
        self.join.last={k:planner[k] for k in EVIDENCE_KEYS}
        self.r.step();self.assertEqual(self.r.state,'completed')
        self.assertEqual(self.g.phase,'active');self.assertEqual(self.g.input_mode,'planner')
        self.assertEqual(self.proc.kills,0);self.assertTrue(self.r.status()['completion_ack'])
        self.assertFalse(self.r.status()['stop_ack']);self.g.protection_executor.cancel.assert_not_called()

    def test_manual_stop_returns_without_claiming_final_frame(self):
        for frame in (None,0):
            with self.subTest(frame=frame):
                self.setUp();self.start()
                sample=deepcopy(self.live_samples[0]);sample['timestamp_monotonic_ns']=int(self.now*1e9)
                sample['replay']=None;self.join.last=sample
                self.r.frame=frame;self.r.commit=deepcopy(sample['output_commit'])
                self.r.return_to_planner();self.r.step()
                self.assertTrue(self.r.returning);self.assertEqual(self.proc.kills,0)
                self.assertIn('cancel_to_planner',[c.args[0] for c in self.client.request.call_args_list])
                sample.update(native_mode=0,token_source='encoder');sample['output_commit']['sequence']+=1
                self.client.request.side_effect=lambda action,**kw: dict(ok=True,state='completed') if action=='release' else dict(ok=True,state='running',phase='settling_planner',return_ready_ns=int(self.now*1e9))
                self.r.step()
                self.assertEqual((self.r.state,self.r.reason,self.r.frame),('completed','operator_stop',frame))
                self.assertTrue(self.r.status()['completion_ack']);self.assertFalse(self.r.status()['stop_ack'])
                self.assertEqual(self.proc.kills,0);self.assertEqual(self.g.input_mode,'planner')

    def test_smpl_mode_cannot_confirm_planner_return(self):
        self.start();self.r.frame=2;self.r.returning=True;self.r.run_started=self.now-4.
        sample=deepcopy(self.live_samples[2])
        sample.update(native_mode=1,token_source='encoder',replay=None,timestamp_monotonic_ns=int(self.now*1e9))
        self.r.commit=deepcopy(sample['output_commit']);sample['output_commit']['sequence']+=1
        self.join.last=sample
        self.client.request.return_value=dict(ok=True,state='running',phase='settling_planner',return_ready_ns=int(self.now*1e9))
        self.r.step()
        self.assertFalse(self.r.status()['completion_ack'])
        self.assertEqual(self.r.reason,'consumption_timeout')
        self.assertNotIn('release',[c.args[0] for c in self.client.request.call_args_list])

    def test_returning_lease_loss_still_triggers_protection(self):
        self.start();self.r.frame=2;self.r.returning=True
        self.client.request.return_value=dict(state='stopping',reason='lease_expired');self.r.step()
        self.assertEqual(self.r.reason,'lease_expired');self.assertEqual(self.proc.kills,1)
        self.assertFalse(self.r.status()['completion_ack']);self.g.protection_executor.cancel.assert_not_called()

    def test_wrong_run_and_owner(self):
        self.start();s=deepcopy(self.live_samples[0]);s['replay']=dict(run=self.r.run+1,frame=0,scheduled_frame=True)
        self.join.last=s;self.r.step();self.assertIsNone(self.r.frame)
        for client,run in [('other',self.r.run),('test',self.r.run+1)]:
            with self.assertRaisesRegex(ValueError,'owner/run'):
                self.g.execution_request('/api/execution/v2/plans/'+self.r.plan['id']+'/stop',
                    dict(client=client,run_id=run,epoch=self.g.epoch,ticket=self.g.ticket(),seq=1))

    def test_teleoperation_mode_exclusion(self):
        self.start()
        for seq,(route,action) in enumerate([('/api/controller','select_pose'),('/api/control',None),('/api/controller','prepare_pose')]):
            x=dict(epoch=self.g.epoch,ticket=self.g.ticket(),client='test',seq=seq,action=action)
            with self.assertRaisesRegex(ValueError,'Replay owns control'):self.g.request(route,x)

    def test_missing_consumed_source_frame_is_not_completion(self):
        self.start();s=deepcopy(self.live_samples[2])
        s['replay']=dict(run=self.r.run,frame=2,scheduled_frame=True)
        s['timestamp_monotonic_ns']=int(self.now*1e9);self.join.last=s;self.r.step()
        self.assertEqual(self.r.reason,'consumption_gap');self.assertFalse(self.r.status()['stop_ack'])

    def test_stale_consumption_does_not_complete(self):
        self.start();s=deepcopy(self.live_samples[2])
        s['replay']=dict(run=self.r.run,frame=2,scheduled_frame=True)
        self.join.last=s;self.r.step();self.assertIsNone(self.r.frame)
        self.assertNotEqual(self.r.reason,'completed')

    def test_delayed_queue_drain_keeps_committed_history(self):
        from collections import deque
        self.start();self.now+=.15
        samples=[]
        for frame,age in ((0,.112),(1,.092)):
            s=deepcopy(self.live_samples[1])
            s['replay']=dict(run=self.r.run,frame=frame,scheduled_frame=True)
            s['timestamp_monotonic_ns']=int((self.now-age)*1e9)
            samples.append(s)
        self.join.last=samples[-1]
        self.g.native_consumption.replay_evidence=deque(samples,maxlen=64)
        self.r._progress()
        self.assertEqual(self.r.frame,1)
        self.assertEqual(self.proc.kills,0)
        self.assertEqual(len(self.g.native_consumption.replay_evidence),0)

    def test_queued_true_gap_still_stops_with_specific_evidence(self):
        from collections import deque
        self.start();self.now+=.15
        s=deepcopy(self.live_samples[1]);s['replay']=dict(run=self.r.run,frame=1,scheduled_frame=True)
        s['timestamp_monotonic_ns']=int((self.now-.112)*1e9)
        self.join.last=s;self.g.native_consumption.replay_evidence=deque([s],maxlen=64)
        self.r._progress()
        self.assertEqual(self.r.reason,'consumption_gap')
        self.assertEqual(json.loads(self.r.detail)['expected_frame'],0)
        self.assertEqual(json.loads(self.r.detail)['received_frame'],1)

    def test_historical_progress_does_not_make_stale_live_feedback_fresh(self):
        from collections import deque
        self.start();self.now+=4
        s=deepcopy(self.live_samples[1]);s['replay']=dict(run=self.r.run,frame=0,scheduled_frame=True)
        s['timestamp_monotonic_ns']=int((self.now-.15)*1e9)
        self.join.last=s;self.g.native_consumption.replay_evidence=deque([s],maxlen=64)
        # Isolate consumption freshness from the separate robot admission gate.
        with patch.object(self.r,'_admit'):
            self.r.step()
        self.assertEqual(self.r.frame,0)
        self.assertEqual(self.r.reason,'consumption_timeout')
        self.assertEqual(self.proc.kills,1)

    def test_aborted_protection_is_not_success(self):
        self.start();self.r.stop('completed');self.p.update(fenced=True,phase=3,reason='feedback_lost')
        self.r.step();self.assertEqual(self.r.state,'failed')
        self.assertEqual(self.r.reason,'protection_aborted:feedback_lost')

    def test_browser_absence_does_not_stop_runtime_owned_replay(self):
        self.start()
        sample=deepcopy(self.live_samples[0])
        sample['replay']=dict(run=self.r.run,frame=0,scheduled_frame=True)
        for _ in range(25):
            self.now+=.2
            for topic in self.g.latest:self.g.latest[topic]=(self.now,self.g.latest[topic][1])
            sample['timestamp_monotonic_ns']=int(self.now*1e9)
            self.join.last=deepcopy(sample)
            self.g.tick();self.r.step()
            self.assertEqual(self.proc.kills,0)
        self.assertEqual(self.r.state,'running')
        self.assertFalse(self.r.status()['browser_lease_required'])
        self.assertGreaterEqual(sum(c.args[0]=='renew' for c in self.client.request.call_args_list),25)

    def test_explicit_estop_supersedes_pending_protection_stop(self):
        self.start();self.r.stop('completed');self.g.halt(estop=True)
        self.assertEqual(self.r.reason,'estop');self.g.protection_executor.cancel.assert_called_once()
        self.p.update(fenced=True,estopped=True,phase=3);self.r.step()
        self.assertEqual(self.r.state,'stopped');self.assertTrue(self.r.status()['stop_ack'])

    def test_live_identity_replacement(self):
        self.start();self.p['recording_identity']['native_executable']='f'*64;self.r.step()
        self.assertEqual(self.r.reason,'runtime_admission_lost');self.assertEqual(self.proc.kills,1)

    def test_http_auth_load_status_wrong_plan(self):
        server=make_server(self.g,'t'*32);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def call(path,data=None,auth=True):
            req=urllib.request.Request('http://127.0.0.1:%d%s'%(server.server_port,path),data=None if data is None else json.dumps(data).encode(),
                headers={'Authorization':'Bearer '+'t'*32} if auth else {})
            with urllib.request.urlopen(req) as res:return json.load(res)
        try:
            path='/api/execution/v2/plans';x=dict(plan=self.plan,client='test',seq=0,epoch=self.g.epoch,ticket=self.g.ticket())
            with self.assertRaises(urllib.error.HTTPError) as e:call(path,x,False)
            self.assertEqual(e.exception.code,401)
            x['padding']=' '* (64*1024*1024)  # HTTP and isolated loader exceed the former cap.
            loaded=call(path,x);self.assertEqual(loaded['state'],'loaded');self.assertEqual(self.proc.kills,0)
            self.assertEqual(call(path+'/'+loaded['plan_id'])['state'],'loaded')
            with self.assertRaises(urllib.error.HTTPError):call(path+'/unknown')
            endpoint=path+'/'+loaded['plan_id']
            def command(seq,**fields):return dict(client='test',seq=seq,epoch=self.g.epoch,ticket=self.g.ticket(),**fields)
            started=call(endpoint+'/start',command(1));self.assertEqual(started['state'],'loading')
            self.r.step()
            with self.assertRaises(urllib.error.HTTPError):call(endpoint+'/renew',command(2,run_id=started['run_id']))
            self.assertFalse(call(endpoint)['browser_lease_required'])
            self.join.last=deepcopy(self.live_samples[0])
            self.join.last['timestamp_monotonic_ns']=int(self.now*1e9)
            stopped=call(endpoint+'/stop',command(3,run_id=started['run_id']));self.assertFalse(stopped['stop_ack'])
            self.r.step();self.assertTrue(self.r.operator_return)
            self.assertEqual(self.proc.kills,0)
            self.assertFalse(call(endpoint)['stop_ack'])
        finally:server.shutdown();server.server_close();thread.join()
