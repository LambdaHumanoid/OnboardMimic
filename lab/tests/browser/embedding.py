"""Real-browser iframe integration test, with in-memory collector and local hosts only.
Run: PYTHONPATH=src python tests/browser/embedding.py [Chrome executable]
Requires optional development dependency playwright; never connects to a robot.
"""
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import threading
from playwright.sync_api import sync_playwright
from onboardmimic_lab.console import make_server


class CollectorFixture:
    def __init__(self):self.actions=[];self.state='idle';self.camera_ready=False;self.feedback_ready=True;self.record_ready=True
    def status(self):return dict(enabled=True,state=self.state,counts={},pending=[],elapsed_s=0,episode_id='fixture',camera=dict(ready=self.camera_ready),error=getattr(self,'error',''))
    def runtime_status(self):return dict(state='active',input_mode='pose',pose_input_ready=True,feedback_continuity=dict(ready=self.feedback_ready),lab=dict(record_ready=self.record_ready))
    def command(self,value):
        self.actions.append(value)
        if value['action']=='start':self.state='recording'
        if value['action']=='stop':self.state='review'
        return self.status()


def start(stack, server):
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def stop():server.shutdown();server.server_close();thread.join()
    stack.callback(stop)
    return 'http://127.0.0.1:'+str(server.server_port)


with ExitStack() as stack:
    target={}
    class Parent(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            raw=('<!doctype html><title>OnboardMimic host fixture</title><iframe title="Lab" src="'+target['url']+'/embed" style="width:100%;height:700px"></iframe>').encode()
            self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    parent=start(stack,ThreadingHTTPServer(('127.0.0.1',0),Parent))
    denied_parent=start(stack,ThreadingHTTPServer(('127.0.0.1',0),Parent))
    client=CollectorFixture()
    target['url']=start(stack,make_server(client,embed_origins=[parent]))
    with sync_playwright() as p:
        options=dict(headless=True,args=['--no-sandbox'])
        if len(sys.argv)>1:options['executable_path']=sys.argv[1]
        browser=p.chromium.launch(**options)
        try:
            page=browser.new_page(viewport={'width':1100,'height':900})
            page.goto(parent)
            frame=page.frame_locator('iframe')
            from playwright.sync_api import expect
            expect(frame.locator('#episode-start')).to_be_disabled()
            assert client.actions==[]
            client.camera_ready=True;client.feedback_ready=False
            expect(frame.locator('#collection-camera-state')).to_have_attribute('data-ready','true')
            expect(frame.locator('#episode-start')).to_be_disabled()
            client.feedback_ready=True;client.record_ready=False
            expect(frame.locator('#collection-robot-state')).to_have_attribute('data-ready','true')
            expect(frame.locator('#episode-start')).to_be_disabled()
            client.record_ready=True
            expect(frame.locator('#episode-start')).to_be_enabled()
            client.error='capture stopped before sources were ready';client.state='failed'
            expect(frame.locator('#collection-state')).to_have_attribute('data-state','failed')
            expect(frame.locator('#collection-error')).not_to_be_empty()
            client.state='discarded'
            expect(frame.locator('#collection-state')).to_have_attribute('data-state','discarded')
            expect(frame.locator('#collection-error')).to_be_empty()
            expect(frame.locator('#episode-start')).to_be_enabled()
            frame.locator('#episode-task').fill('iframe integration fixture')
            frame.locator('#episode-start').click()
            frame.locator('#collection-state[data-state="recording"]').wait_for()
            assert client.actions==[dict(action='start',task_name='',task_description='iframe integration fixture',collector_name='')]
            # Reload just the iframe: collector remains recording, no new command.
            page.locator('iframe').evaluate('(el)=>{el.src=el.src}')
            frame.locator('#collection-state[data-state="recording"]').wait_for()
            assert len(client.actions)==1
            frame.locator('#episode-stop').click()
            frame.locator('#collection-state[data-state="review"]').wait_for()
            page.goto(denied_parent,wait_until='networkidle')
            assert page.frame_locator('iframe').locator('#collection-panel').count()==0
            assert len(client.actions)==2
            print('Browser PASS: allowlisted cross-port iframe works; recording survives iframe reload; different parent port blocked.')
        finally:browser.close()
