import json
import socket
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path

from onboardmimic.backends.sonic.native.consumption import NativeJoin, controller_spec, RAW_TOPIC, TOPIC
from onboardmimic.backends.sonic.native.receiver import Receiver
from onboardmimic.core.execution.software import fingerprint


def fixture():
    """Explicit fake inference/bridge data; not actual loaded model identities."""
    kinds = [('token_state',64),('his_base_angular_velocity_10frame_step1',30),
             ('his_body_joint_positions_10frame_step1',290),('his_body_joint_velocities_10frame_step1',290),
             ('his_last_actions_10frame_step1',290),('his_gravity_dir_10frame_step1',30)]
    layout=[];offset=0
    for name, size in kinds:
        layout.append(dict(name=name, offset=offset, dimension=size));offset+=size
    names=['decoder_loaded_trt','decoder_onnx_cache_validated','native_executable','loaded_parameters',
           'loaded_observation_layout','loaded_encoder_layout']
    native=dict(schema='onboardmimic.sonic.native-manifest',schema_version=2,layout=layout,
        input_dimension=offset,period_ns=20000000,encoder_layout=[],
        parameters=dict(is_using_encoder=False,mujoco_to_isaaclab=list(range(29))),
        artifacts={n:fingerprint('TEST_FIXTURE_'+n) for n in names},test_fixture=True)
    bridge=dict(schema='onboardmimic.sonic.bridge-manifest',schema_version=2,physical=False,
                protected_writer=True,artifacts={'bridge_executable':fingerprint('TEST_FIXTURE_BRIDGE')},test_fixture=True)
    now=1000000000
    command={k:[.2]*29 for k in ('q','dq','kp','kd','tau')}
    times=[780000000+i*20000000+i*10000 for i in range(10)]
    histories=[dict(ns=[],index=[])]+[dict(ns=list(times),index=list(range(10)),low_state_received_ns=[t-1000 for t in times],torso_imu_received_ns=[t-2000 for t in times]) for _ in range(5)]
    attempt=dict(kind=0,index=1,generation=1,reset_ns=700000000,at_ns=980000000,
        realtime_ns=1800000000000000000,finished_ns=982000000,inference_ok=True,command_ok=True,
        mode=2,input=[i*.001 for i in range(offset)],histories=histories,raw_action=[.1]*29,command=deepcopy(command))
    mapping=dict(kind=1,index=1,generation=1,stream=111,seq=222,send_ns=984000000)
    commit=dict(kind=2,index=0,generation=9,stream=111,seq=222,send_ns=984000000,at_ns=986000000,
                authority=True,command=deepcopy(command))
    return native,bridge,attempt,mapping,commit,now


class NativeConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.native,self.bridge,self.attempt,self.mapping,self.commit,self.now=fixture()
        self.join=NativeJoin(clock_ns=lambda:self.now)
        self.emit('native',dict(kind='manifest',manifest=self.native))
        self.emit('bridge',dict(kind='manifest',manifest=self.bridge))

    def emit(self,source,event):
        return self.join.ingest(source,dict(event,native_session=source+'-TEST_FIXTURE',published_monotonic_ns=self.now))

    def test_replay_consumption_requires_protected_commit_and_keeps_live_history(self):
        self.attempt['mode']=3
        self.attempt['replay']=dict(run=42,frame=7,scheduled_frame=True)
        self.emit('native',self.attempt);self.emit('native',self.mapping)
        self.assertIsNone(self.join.last)
        sample=self.emit('bridge',self.commit)
        self.assertEqual(sample['replay']['frame'],7)
        self.assertEqual(sample['decoder_input'],self.attempt['input'])
        self.assertEqual(sample['state_history_ns']['his_gravity_dir_10frame_step1'],self.attempt['histories'][-1]['ns'])
        self.assertIsNone(sample['encoder_input'])
        self.assertFalse(self.join.status()['record_ready'])

    def test_replay_changed_output_is_not_execution(self):
        self.attempt['mode']=3;self.attempt['replay']=dict(run=42,frame=0,scheduled_frame=True)
        self.commit['command']['q'][0]+=1
        self.emit('native',self.attempt);self.emit('native',self.mapping)
        self.assertIsNone(self.emit('bridge',self.commit))

    def consume(self):
        self.emit('native',self.attempt)
        self.emit('native',self.mapping)
        return self.emit('bridge',self.commit)

    def test_default_inputs_and_outputs_remain_isolated(self):
        self.emit('native',self.attempt)
        self.attempt['input'][0]=999
        self.emit('native',self.mapping)
        sample=self.emit('bridge',self.commit)
        self.assertEqual(sample['decoder_input'][0],0.)
        sample['control_output']['q'][0]=999
        self.assertEqual(self.join.last['control_output']['q'][0],.2)

    def test_owned_events_match_copying_path_and_survive_later_frames(self):
        expected=self.consume()
        self.setUp()
        def transfer(source,event):
            return self.join.ingest(source,dict(deepcopy(event),
                native_session=source+'-TEST_FIXTURE',published_monotonic_ns=self.now),take_ownership=True)
        transfer('native',self.attempt);transfer('native',self.mapping)
        sample=transfer('bridge',self.commit)
        self.assertEqual(sample,expected)
        self.assertIs(sample,self.join.last)
        saved=deepcopy(sample)
        self.attempt['index']=self.mapping['index']=2
        self.mapping['seq']=self.commit['seq']=223
        self.attempt['input'][0]=.5
        transfer('native',self.attempt);transfer('native',self.mapping)
        transfer('bridge',self.commit)
        self.assertEqual(sample,saved)
        self.assertEqual(self.join.last['decoder_input'][0],.5)

    def test_all_evidence_arrival_orders_and_repeated_500hz_commands(self):
        from itertools import permutations
        for order in permutations((0, 1, 2)):
            with self.subTest(order=order):
                self.setUp()
                events=[('native',self.attempt),('native',self.mapping),('bridge',self.commit)]
                results=[self.emit(*events[i]) for i in order]
                self.assertEqual(sum(v is not None for v in results),1)
                self.assertEqual(self.join.last_index,1)
                for seq in range(223,233):
                    self.assertIsNone(self.emit('native',dict(self.mapping,seq=seq)))
                    self.assertIsNone(self.emit('bridge',dict(self.commit,seq=seq)))
                # Duplicated sends must not renew the inference timestamp.
                self.now+=61000000
                self.assertFalse(self.join.status()['native_consumption_fresh'])
                self.assertIsNone(self.emit('native',dict(self.mapping,seq=234)))
                self.assertFalse(self.join.status()['native_consumption_fresh'])

    def test_float_input_token_and_postprocessed_output(self):
        sample=self.consume();self.assertIsNotNone(sample)
        self.assertEqual(sample['reference']['token_state'],self.attempt['input'][:64])
        self.assertEqual(sample['state']['his_base_angular_velocity_10frame_step1'][0],self.attempt['input'][64:67])
        self.assertEqual(sample['raw_policy_action'],[.1]*29)
        self.assertEqual(sample['control_output']['q'],[.2]*29)
        self.assertEqual(sample['timestamp_monotonic_ns'],self.attempt['at_ns'])
        self.assertGreater(sample['output_commit']['timestamp_monotonic_ns'],sample['timestamp_monotonic_ns'])
        self.assertTrue(self.join.status()['native_consumption_fresh'])
        self.assertFalse(self.join.status()['record_ready'])

    def test_encoder_snapshot_and_actual_yaml_layout(self):
        import re
        root=Path(__file__).resolve().parents[2]
        yaml=(root/'onboardmimic/backends/sonic/deploy/policy/release/observation_config.yaml').read_text().split('encoder:')[0]
        enabled=re.findall(r'name: "([^"]+)"\s+enabled: true',yaml)
        self.assertEqual(enabled,[f['name'] for f in self.native['layout']])
        self.native['parameters']['is_using_encoder']=True
        self.native['encoder_layout']=[dict(name='encoder_mode_4',offset=0,dimension=4)]
        self.native['artifacts'].update(encoder_loaded_trt='a'*64,encoder_onnx_cache_validated='b'*64)
        self.emit('native',dict(kind='manifest',manifest=self.native))
        self.attempt['encoder_input']=[0.,0.,1.,0.]
        self.attempt['encoder_at_ns']=979000000
        sample=self.consume()
        self.assertEqual(sample['encoder_input'],[0.,0.,1.,0.])
        self.assertEqual(sample['decoder_input'],self.attempt['input'])

    def test_actual_irregular_history_and_dimensions(self):
        sample=self.consume();name='his_body_joint_positions_10frame_step1'
        self.assertEqual(self.join.spec['state'][name]['shape'],[10,29])
        self.assertEqual(sample['state_history_ns'][name],self.attempt['histories'][2]['ns'])
        self.assertNotEqual(sample['state_history_ns'][name][1]-sample['state_history_ns'][name][0],20000000)
        self.assertEqual(self.native['input_dimension'],994)

    def test_failures_and_no_publish_only_consumption(self):
        self.emit('native',self.attempt)
        self.assertIsNone(self.emit('native',self.mapping))
        self.assertIsNone(self.join.last)
        self.commit['authority']=False
        self.assertIsNone(self.emit('bridge',self.commit));self.assertIsNone(self.join.last)
        self.attempt['index']=2;self.attempt['inference_ok']=False
        self.emit('native',self.attempt);self.assertFalse(self.join.status()['native_consumption_fresh'])

    def test_output_or_sequence_mismatch_rejected(self):
        self.commit['command']['q'][0]=.3
        self.assertIsNone(self.consume())
        self.commit['command']['q'][0]=.2;self.commit['seq']=223
        self.assertIsNone(self.consume())

    def test_warmup_padding_and_unobserved_history(self):
        self.attempt['histories'][1]['ns']=[]
        self.assertIsNone(self.consume())
        self.attempt=fixture()[2];self.attempt['reset_ns']=900000000
        self.assertIsNone(self.consume())
        self.attempt=fixture()[2];self.attempt['histories'][2]['ns'][0]=0
        with self.assertRaises(ValueError): self.consume()

    def test_reset_freshness_and_protection_generation(self):
        sample=self.consume();old=sample['session_id']
        self.now+=61000000;self.assertFalse(self.join.status()['native_consumption_fresh'])
        self.now=1000000000
        self.attempt['index']=2;self.attempt['generation']=2
        self.mapping.update(index=2,generation=2,seq=223);self.commit.update(seq=223,generation=10)
        sample=self.consume();self.assertNotEqual(old,sample['session_id'])
        self.commit['authority']=False;self.emit('bridge',self.commit)
        self.assertFalse(self.join.status()['native_consumption_fresh'])

    def test_bounded_missing_feedback_and_drops_keep_index(self):
        for i in range(1,400):
            a=dict(self.attempt,index=i);self.emit('native',a)
        self.assertLessEqual(len(self.join.pending),256)
        self.attempt['index']=500;self.mapping['index']=500
        self.assertEqual(self.consume()['index'],500)

    def test_contract_changes_and_unknown_fields_fail(self):
        old=fingerprint(self.join.spec)
        for change in ('artifact','mapping'):
            native=deepcopy(self.native)
            if change=='artifact':native['artifacts']['decoder_loaded_trt']='f'*64
            else:native['parameters']['mujoco_to_isaaclab'].reverse()
            self.assertNotEqual(old,fingerprint(controller_spec(native,self.bridge)))
        bad=deepcopy(self.native);bad['layout'][1]['name']='unknown_state'
        with self.assertRaises(ValueError): controller_spec(bad,self.bridge)
        self.bridge['protected_writer']=False
        with self.assertRaises(ValueError): controller_spec(self.native,self.bridge)

    def test_native_restart_revokes_previous_evidence(self):
        self.consume()
        self.join.ingest('native',dict(kind='manifest',manifest=self.native,native_session='NEW_NATIVE',published_monotonic_ns=self.now))
        self.assertIsNone(self.join.last)
        self.assertIsNone(self.consume())  # old native session cannot be reused

    def test_out_of_order_feedback_and_fault_cannot_revive_old_attempt(self):
        self.emit('bridge',self.commit)
        self.emit('native',self.mapping)
        self.assertIsNotNone(self.emit('native',self.attempt))
        self.attempt['index']=2;self.mapping['index']=2;self.mapping['seq']=223;self.commit['seq']=223
        self.emit('native',self.attempt);self.emit('native',self.mapping)
        failed=dict(self.attempt,index=3,inference_ok=False)
        self.emit('native',failed)
        self.assertIsNone(self.emit('bridge',self.commit))
        self.assertFalse(self.join.status()['native_consumption_fresh'])

    def test_real_receiver_two_frame_output_and_manifest_export(self):
        import zmq
        self.native['parameters']['is_using_encoder']=True
        self.native['encoder_layout']=[dict(name='encoder_mode_4',offset=0,dimension=4)]
        self.native['artifacts'].update(encoder_loaded_trt='b'*64,encoder_onnx_cache_validated='c'*64)
        self.attempt.update(encoder_input=[0.,0.,0.,0.],encoder_at_ns=979000000)
        ports=[];holders=[]
        for _ in range(3):
            s=socket.socket();s.bind(('127.0.0.1',0));ports.append(s.getsockname()[1]);holders.append(s)
        for s in holders:s.close()
        context=zmq.Context();pubs=[context.socket(zmq.PUB) for _ in range(2)]
        for p,port in zip(pubs,ports):p.bind('tcp://127.0.0.1:'+str(port))
        sub=context.socket(zmq.SUB);sub.setsockopt(zmq.SUBSCRIBE,TOPIC);sub.connect('tcp://127.0.0.1:'+str(ports[2]))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spec.json';receiver=Receiver(*ports,export_spec=path);receiver.thread.start()
            try:
                observed={}
                for i in range(30):
                    now=time.monotonic_ns();delta=now-self.now
                    attempt=deepcopy(self.attempt);mapping=deepcopy(self.mapping);commit=deepcopy(self.commit)
                    for e in (attempt,mapping,commit):
                        for key in ('at_ns','finished_ns','send_ns','reset_ns','encoder_at_ns'):
                            if key in e:e[key]+=delta
                    for h in attempt['histories']:
                        for key in ('ns','low_state_received_ns','torso_imu_received_ns'):
                            if key in h:h[key]=[v+delta for v in h[key]]
                    attempt['index']=mapping['index']=i+1;mapping['seq']=commit['seq']=i+1
                    attempt['mode']=(0,1,2,3)[i%4]
                    if attempt['mode']==3:attempt['replay']=dict(run=1,frame=i,scheduled_frame=True)
                    for src,event in [(0,dict(kind='manifest',manifest=self.native)),(1,dict(kind='manifest',manifest=self.bridge)),(0,attempt),(0,mapping),(1,commit)]:
                        event=dict(event,native_session=('native' if src==0 else 'bridge')+'-TEST',published_monotonic_ns=now)
                        pubs[src].send_multipart([RAW_TOPIC,json.dumps(event).encode()])
                    if sub.poll(50):
                        frames=sub.recv_multipart();self.assertEqual(len(frames),2);self.assertEqual(frames[0],TOPIC)
                        sample=json.loads(frames[1]);self.assertEqual(sample['reference_evidence'],'consumed')
                        observed[sample['native_mode']]=sample
                        # Inspect the parent's evidence, not only the child's public stream.
                        deadline=time.monotonic()+1.
                        while time.monotonic()<deadline:
                            with receiver.lock:last=deepcopy(receiver.join.last)
                            if last and last['index']==sample['index']:break
                            time.sleep(.002)
                        self.assertIsNotNone(last)
                        self.assertEqual(last['index'],sample['index'])
                        self.assertEqual(last['native_mode'],sample['native_mode'])
                        self.assertEqual(last['token_source'],'replay' if sample['native_mode']==3 else 'encoder')
                        if set(observed)=={0,1,2,3}:break
                self.assertEqual(set(observed),{0,1,2,3})
                self.assertEqual(len({v['session_id'] for v in observed.values()}),1)
                self.assertTrue(receiver.replay_evidence)
                self.assertEqual(json.loads(path.read_text())['schema_version'],2)
                self.assertFalse(receiver.status()['record_ready'])
            finally:
                receiver.close()
                for s in pubs+[sub]:s.close(0)
                context.term()


if __name__=='__main__':unittest.main()
