from export_test_helpers import wait_export
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from onboardmimic_lab.collection import Collector
from onboardmimic_lab.collector_service import make_server as make_collector, CollectorClient
from onboardmimic_lab.console import make_server as make_console
from onboardmimic_lab.runtime_status import RuntimeStatus
from test_collection import fixture_config, active, fill, make_video


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for name in ('encoder','decoder'):(self.root/name).write_bytes(name.encode())
        self.video=make_video(self.root/'input.ts')
        def worker(raw):
            fill(raw,self.video)
            self.collector.stop_event.wait(10)
        self.collector=Collector(fixture_config(self.root/'data'),Mock(return_value=active()),worker=worker)
        self.addCleanup(self.collector.close)
        self.socket=self.root/'collector.sock'
        self.daemon=make_collector(self.collector,self.socket)
        self.thread=threading.Thread(target=self.daemon.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop_server,self.daemon,self.thread)
        self.client=CollectorClient(self.socket)
    def stop_server(self,s,t):s.shutdown();s.server_close();t.join()
    def web(self):
        s=make_console(self.client);t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
        return s,t,'http://127.0.0.1:'+str(s.server_port)
    def request(self,url,action=None,token=None,origin=None):
        headers={}
        if token:headers['X-Control-Token']=token
        if origin:headers['Origin']=origin
        body=None if action is None else json.dumps(action).encode()
        with urlopen(Request(url,data=body,headers=headers),timeout=3) as r:return json.load(r)
    def wait_state(self,state):
        end=time.monotonic()+5
        while time.monotonic()<end:
            if self.client.status()['state']==state:return
            time.sleep(.01)
        self.fail(str(self.client.status()))
    def test_recording_survives_web_restart_and_can_finish_save(self):
        s,t,url=self.web()
        try:
            token=self.request(url+'/api/session')['token']
            status=self.request(url+'/api/collection',{'action':'start','task':'restart browser'},token,url)
            episode=status['episode_id']
        finally:self.stop_server(s,t)
        self.assertFalse(self.collector.stop_event.is_set())
        self.assertTrue(self.collector.thread.is_alive())
        s,t,url2=self.web()
        try:
            self.assertEqual(self.request(url2+'/api/collection')['episode_id'],episode)
            with self.assertRaises(HTTPError) as error:self.request(url2+'/api/collection',{'action':'stop'},token,url2)
            self.assertEqual(error.exception.code,403)
            token2=self.request(url2+'/api/session')['token']
            self.request(url2+'/api/collection',{'action':'stop'},token2,url2)
            self.wait_state('review')
            self.request(url2+'/api/collection',{'action':'save'},token2,url2)
            self.wait_state('saved')
            wait_export(self.root/'data/episodes'/episode)
            self.assertTrue((self.root/'data/episodes'/episode/'aligned/frames.jsonl').exists())
        finally:self.stop_server(s,t)
    def test_socket_ownership_and_duplicate_server(self):
        self.assertEqual(self.socket.stat().st_mode&0o777,0o600)
        with self.assertRaises(OSError):make_collector(self.collector,self.socket)
        self.assertEqual(self.client.status()['state'],'idle')
    def test_origin_host_and_routes(self):
        s,t,url=self.web()
        try:
            token=self.request(url+'/api/session')['token']
            with self.assertRaises(HTTPError) as e:self.request(url+'/api/collection',{'action':'start'},token,'https://evil.invalid')
            self.assertEqual(e.exception.code,403)
            with self.assertRaises(HTTPError) as e:self.request(url+'/api/controller',{'action':'prepare_pose'},token,url)
            self.assertEqual(e.exception.code,404)
            with self.assertRaises(HTTPError) as e:urlopen(Request(url+'/api/session',headers={'Host':'evil.invalid'}))
            self.assertEqual(e.exception.code,403)
            self.assertEqual(self.client.status()['state'],'idle')
        finally:self.stop_server(s,t)
    def test_missing_daemon_never_starts_a_replacement(self):
        client=CollectorClient(self.root/'missing.sock')
        s=make_console(client);t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
        try:
            with self.assertRaises(HTTPError) as e:self.request('http://127.0.0.1:%s/api/collection'%s.server_port)
            self.assertEqual(e.exception.code,503)
            self.assertFalse((self.root/'missing.sock').exists())
        finally:self.stop_server(s,t)
    def test_runtime_adapter_only_gets_status_and_removes_tickets(self):
        token=self.root/'token';token.write_text('s'*32);token.chmod(0o600)
        adapter=RuntimeStatus({'token_file':str(token)})
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps(dict(active(),ticket='secret',epoch='secret',lab=dict(record_ready=True,contract_sha256='a'*64,session_id='public',ticket='secret',diagnostics={'last_index':2369,'last_source_ns':123},recording_validation={'failures':['recording_status_snapshot_expired'],'snapshot_age_ms':300}))).encode()
        with patch('onboardmimic_lab.runtime_status.urlopen',return_value=response) as call:
            value=adapter()
            self.assertNotIn('ticket',value);self.assertNotIn('epoch',value)
            self.assertEqual(value['lab'],dict(record_ready=True,contract_sha256='a'*64,session_id='public',diagnostics={'last_index':2369,'last_source_ns':123},recording_validation={'failures':['recording_status_snapshot_expired'],'snapshot_age_ms':300}))
            req=call.call_args[0][0];self.assertEqual(req.get_method(),'GET')
            self.assertEqual(req.full_url,'http://127.0.0.1:18921/api/controller')


class IsolationTest(unittest.TestCase):
    def test_new_services_do_not_import_external_runtime(self):
        import subprocess, sys
        subprocess.run([sys.executable,'-c',
            'import onboardmimic_lab.console, onboardmimic_lab.collector_service; import sys; '
            'assert not any(k == "onboardmimic" or k.startswith("onboardmimic.") for k in sys.modules)'],check=True)
