"""Browser HTTP -> console -> both client adapters -> runtime; fake robot only."""
import json,threading,unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from onboardmimic.console.server import Console,make_server
from onboardmimic.console.client import OnboardBackend
from onboardmimic.core.client import OnboardClient
from onboardmimic.core.gateway import make_server as runtime_server
from tests.runtime import test_native_lifecycle as fixtures

class ConfirmationRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.NativeTest();self.f.setUp();self.f.g.physical=True
        self.executor=Mock();self.executor.snapshot.return_value=dict(configured=True,fresh=True,armed=True)
        self.f.g.protection_executor=self.executor
        runtime=runtime_server(self.f.g,'test-only-not-a-real-token-1234567890')
        client=OnboardClient('http://127.0.0.1:'+str(runtime.server_port),'test-only-not-a-real-token-1234567890')
        connection=Mock();connection.status.side_effect=lambda s,runner:dict(s,setup_ready=True,setup_available=False)
        tracking=Mock(id='test',label='test');tracking.status.return_value={}
        self.console=Console(OnboardBackend(client),Path(__file__).resolve().parents[2]/'onboardmimic/console/static',connection=connection,tracking=tracking)
        console=make_server(self.console);self.url='http://127.0.0.1:'+str(console.server_port)
        self.servers=[runtime,console];self.threads=[]
        for server in self.servers:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.threads.append(thread)
    def tearDown(self):
        for s in self.servers:s.shutdown();s.server_close()
        for t in self.threads:t.join()
    def post(self,action,**fields):
        request=Request(self.url+'/api/controller',data=json.dumps(dict(action=action,**fields)).encode(),headers={'Content-Type':'application/json','X-Control-Token':self.console.token})
        with urlopen(request,timeout=3) as r:return json.load(r)
    def prepare(self):
        self.post('prepare_pose',confirm_experimental_protection=True);self.f.logs='Init Done';self.f.g.tick()
    def test_prepare_requires_explicit_confirmation_through_http(self):
        for value in (None,False,'true',1):
            with self.assertRaises(HTTPError):self.post('prepare_pose',confirm_experimental_protection=value)
        self.assertEqual(self.f.launches,0)
        self.prepare();self.executor.arm.assert_called_once_with(self.f.proc.pid)
    def test_start_confirmation_survives_complete_http_chain(self):
        self.prepare()
        for value in (None,False,'true',1):
            with self.assertRaises(HTTPError):self.post('start_policy',confirm_experimental_protection=value)
        self.assertEqual(self.f.g.phase,'prepared')
        self.assertEqual(self.post('start_policy',confirm_experimental_protection=True)['state'],'starting_policy')
        self.f.logs+='\nCONTROL armed\nBumpless policy handoff complete';self.f.g.tick()
        self.executor.arm.assert_called_once_with(self.f.proc.pid)
    def test_landing_confirmation_survives_chain_but_is_never_synthesized(self):
        self.prepare();self.post('start_policy',confirm_experimental_protection=True)
        self.f.logs+='\nCONTROL armed\nBumpless policy handoff complete';self.f.g.tick()
        self.f.g.allow_wbc_exit_tests=True;sent=[];self.f.proc.send_signal=sent.append
        with self.assertRaises(HTTPError):self.post('test_wbc_sigterm')
        self.assertEqual(sent,[])
        self.post('test_wbc_sigterm',confirm_landing_execution=True)
        self.assertEqual(len(sent),1)
