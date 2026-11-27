"""Local readiness tests with explicit substituted evidence, never robot motion."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import socket
import tempfile
import threading
import json
import unittest
from unittest.mock import Mock, patch

from onboardmimic.backends.sonic.native.consumption import NativeJoin
from onboardmimic.backends.sonic.native.receiver import Receiver
from onboardmimic.core.protection.client import ProtectionClient
from tests.sonic.test_native_consumption import fixture
from tests.runtime import test_native_lifecycle as native_fixture


class RecordReadyTests(unittest.TestCase):
    def setUp(self):
        self.native,self.bridge,self.a,self.m,self.c,self.now=fixture()
        # Unit-only stand-ins for native evidence. Production fixture manifests
        # stay marked and are tested separately below.
        self.native.pop('test_fixture');self.bridge.pop('test_fixture')
        self.bridge['physical']=True
        self.join=NativeJoin(clock_ns=lambda:self.now)
        self.emit('native',dict(kind='manifest',manifest=self.native))
        self.emit('bridge',dict(kind='manifest',manifest=self.bridge))
        self.runtime=dict(active=True,pid=123,input_mode='pose',mode_switch_pending=False,
            feedback_ready=True,pose_input_ready=True,physical=True,
            protection=dict(configured=True,fresh=True,armed=True,fenced=False,estopped=False,
                takeover_requested=False,error='',wbc_pid=123,bridge_pid=456,generation=9,stream=111,
                recording_identity=dict(wbc_pid=123,bridge_pid=456,generation=9,
                    native_executable=self.native['artifacts']['native_executable'],
                    bridge_executable=self.bridge['artifacts']['bridge_executable'])))

    def emit(self,source,event):
        return self.join.ingest(source,dict(event,native_session=source+'-LOCAL_UNIT_TEST',published_monotonic_ns=self.now))

    def consume(self):
        self.emit('native',self.a);self.emit('native',self.m);return self.emit('bridge',self.c)

    def ready(self):return self.join.status(self.runtime)['record_ready']

    def test_requires_consumption_and_bound_runtime(self):
        self.assertFalse(self.ready())
        self.emit('native',self.a);self.emit('native',self.m)
        self.assertFalse(self.ready())
        self.emit('bridge',self.c)
        self.assertTrue(self.ready())
        self.assertFalse(self.join.status()['record_ready'])
        self.now+=60000001;self.assertFalse(self.ready())

    def test_runtime_mode_stop_feedback_and_physical_checks(self):
        self.consume()
        for key,value in [('active',False),('pid',124),
                          ('feedback_ready',False),('physical',False)]:
            runtime=deepcopy(self.runtime);runtime[key]=value
            with self.subTest(key=key):self.assertFalse(self.join.status(runtime)['record_ready'])
        self.assertTrue(self.ready())

    def test_planner_pose_replay_share_recording_session(self):
        session=None
        for index,mode in enumerate((0,1,2,1,2,3),1):
            self.a.update(index=index,mode=mode)
            self.m.update(index=index,seq=221+index);self.c['seq']=221+index
            if mode==3:self.a['replay']=dict(run=1,frame=0,scheduled_frame=True)
            self.runtime.update(input_mode='replay' if mode==3 else 'pose' if mode==2 else 'planner',
                                mode_switch_pending=True,pose_input_ready=False)
            sample=self.consume();self.assertIsNotNone(sample)
            self.assertTrue(self.ready(),self.join.status(self.runtime))
            self.assertEqual(sample['native_mode'],mode)
            if session is None:session=sample['session_id']
            self.assertEqual(sample['session_id'],session)
        self.runtime['protection']['takeover_requested']=True
        self.assertFalse(self.ready())

    def test_protection_and_process_identity_must_match(self):
        self.consume()
        for key,value in [('configured',False),('fresh',False),('armed',False),('fenced',True),
                          ('estopped',True),('takeover_requested',True),('error','offline'),('wbc_pid',124),
                          ('bridge_pid',457),('generation',10),('stream',112),('recording_identity',{})]:
            runtime=deepcopy(self.runtime);runtime['protection'][key]=value
            with self.subTest(key=key):self.assertFalse(self.join.status(runtime)['record_ready'])
        for key in ('native_executable','bridge_executable','wbc_pid','bridge_pid','generation'):
            runtime=deepcopy(self.runtime);runtime['protection']['recording_identity'][key]='wrong'
            with self.subTest(identity=key):self.assertFalse(self.join.status(runtime)['record_ready'])

    def test_fixture_model_session_and_spec_changes_revoke(self):
        self.consume();self.assertTrue(self.ready())
        self.join.last['reference_evidence']='submitted';self.assertFalse(self.ready())
        self.join.last['reference_evidence']='consumed'
        self.join.last['session_id']='wrong';self.assertFalse(self.ready())
        self.join.last=None;self.join.last_index=-1;self.consume()
        self.join.last['contract_sha256']='f'*64;self.assertFalse(self.ready())
        self.native['test_fixture']=True
        self.emit('native',dict(kind='manifest',manifest=self.native));self.consume()
        self.assertEqual(self.join.status(self.runtime)['reason'],'fixture_not_recordable')
        self.native.pop('test_fixture');self.native['artifacts']['decoder_loaded_trt']='c'*64
        self.emit('native',dict(kind='manifest',manifest=self.native));self.assertFalse(self.ready())

    def test_fault_takeover_wrong_mode_and_bad_history_revoke(self):
        for failure in ('inference','takeover','mode','history'):
            self.setUp();self.consume();self.assertTrue(self.ready())
            if failure=='takeover':
                self.c['authority']=False;self.emit('bridge',self.c)
            else:
                self.a['index']=2;self.m.update(index=2,seq=223);self.c['seq']=223
                if failure=='inference':self.a['inference_ok']=False
                if failure=='mode':self.a['mode']='invalid'
                if failure=='history':self.a['histories'][1]['ns']=[]
                self.consume()
            with self.subTest(failure=failure):self.assertFalse(self.ready())

    def test_stale_status_keeps_timing_evidence_without_refreshing_sample(self):
        receiver=Receiver(17689,17690,17691);receiver.join=self.join
        self.consume()
        source=self.join.last['timestamp_monotonic_ns']
        receiver.diagnostics={'join':{'index':1,'source_ns':source},'ipc_received_ns':source+70_000_000}
        with patch.object(self.join,'clock_ns',return_value=source+70_000_000):
            result=receiver.status(self.runtime)
        self.assertFalse(result['record_ready'])
        self.assertEqual(result['reason'],'native_consumption_stale_or_warming')
        self.assertEqual(result['diagnostics']['last_source_ns'],source)
        self.assertEqual(result['diagnostics']['stages']['ipc_received_ns'],source+70_000_000)
        self.assertNotIn('decoder_input',result['diagnostics'])

    def test_receiver_error_and_close_revoke(self):
        receiver=Receiver(17689,17690,17691);receiver.join=self.join
        self.consume();self.assertTrue(receiver.status(self.runtime)['record_ready'])
        receiver.error='OSError';self.assertFalse(receiver.status(self.runtime)['record_ready'])
        receiver.error=None;receiver.close();self.assertFalse(receiver.status(self.runtime)['record_ready'])

    def test_real_lifecycle_projects_current_runtime(self):
        fixture=native_fixture.NativeTest();fixture.setUp();fixture.activate();g=fixture.g
        g.input_mode='pose';g.pose_reader=Mock()
        g.pose_reader.snapshot.return_value={'ready':True,'reason':''}
        g.pose_reader.skeleton.return_value=None
        g.physical=True
        g.protection_executor=Mock();g.protection_executor.snapshot.return_value=self.runtime['protection']
        receiver=Receiver(17689,17690,17691);receiver.join=self.join;g.native_consumption=receiver
        self.consume()
        with patch.object(g,'tick'):
            self.assertTrue(g.status()['lab']['record_ready'])
            g.input_mode='planner';self.assertTrue(g.status()['lab']['record_ready'])
            g.mode_pending=True;self.assertTrue(g.status()['lab']['record_ready'])
            g.input_mode='pose';g.phase='stopped';self.assertFalse(g.status()['lab']['record_ready'])
        self.assertEqual(fixture.proc.kills,0)


class ProcessIdentityTests(unittest.TestCase):
    def test_live_proc_hash_and_cache(self):
        client=ProtectionClient.__new__(ProtectionClient);client.identity_cache={}
        digest=client._executable_identity('native',os.getpid())
        self.assertEqual(digest,hashlib.sha256(Path('/proc/self/exe').read_bytes()).hexdigest())
        with patch('onboardmimic.core.protection.client.hashlib.sha256',side_effect=AssertionError('must not rehash')):
            self.assertEqual(client._executable_identity('native',os.getpid()),digest)
        with self.assertRaises(ValueError):client._executable_identity('native',0)

    def test_unix_peer_pid_is_kernel_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            path=directory+'/protection.sock'
            server=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);server.bind(path);server.listen(1)
            def respond():
                conn,_=server.accept()
                with conn:
                    conn.recv(8192)
                    conn.send(json.dumps(dict(ok=True,protocol=1,bridge_pid=999999)).encode())
            thread=threading.Thread(target=respond);thread.start()
            client=ProtectionClient.__new__(ProtectionClient);client.path=path;client._init_diagnostics()
            try:
                result=client._request('status')
                self.assertEqual(result['bridge_pid'],os.getpid())
            finally:thread.join(timeout=1);server.close()


if __name__=='__main__':unittest.main()
