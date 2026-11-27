"""Real OM host + Lab iframe + actual Collector/save, with synthetic sensor data.
Usage: PYTHONPATH=src python tests/browser/om_integration.py OM_ROOT OUTPUT [CHROME]
No robot connections: OM backend, tracking and connection are test doubles.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import Mock
from playwright.sync_api import sync_playwright, expect

from onboardmimic_lab.collection import Collector
from onboardmimic_lab.collector_service import CollectorClient, make_server as collector_server
from onboardmimic_lab.console import make_server as lab_server
from onboardmimic_lab.dataset import read_aligned

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_collection import fixture_config, active, fill, make_video
om=Path(sys.argv[1]).resolve();output=Path(sys.argv[2]).resolve();output.mkdir(parents=True,exist_ok=False)
sys.path.insert(0,str(om))
from onboardmimic.console.server import Console, make_server as om_server
from onboardmimic.console.lab_embed import describe


def start(stack,server):
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def stop():server.shutdown();server.server_close();thread.join()
    stack.callback(stop)
    return 'http://127.0.0.1:'+str(getattr(server,'server_port',0))


with ExitStack() as stack:
    tmp=Path(stack.enter_context(tempfile.TemporaryDirectory()))
    video=make_video(output/'fixture.ts')
    for name in ('encoder','decoder'):(output/name).write_text('fixture model')
    def worker(raw):
        fill(raw,video)
        with collector.lock:collector.state='recording';collector.started=time.monotonic()
        collector.stop_event.wait(60)
    def ready_runtime():
        value=active();value['feedback_continuity']={'ready':True};value.setdefault('lab',{})['record_ready']=True
        return value
    collector=Collector(fixture_config(output/'data'),ready_runtime,worker=worker)
    # This worker supplies a pre-generated video fixture, not live UDP ingress.
    from types import SimpleNamespace
    collector.camera=SimpleNamespace(status=lambda:dict(ready=True),close=lambda:None)
    stack.callback(collector.close)
    start(stack,collector_server(collector,tmp/'collector.sock'))
    client=CollectorClient(tmp/'collector.sock')
    backend=Mock();backend.status.side_effect=lambda:dict(active(),robot='G1 fixture',mode='onboard_real',pids=[],capabilities=['planner','pose'],planner_control_allowed=False)
    connection=Mock();connection.status.side_effect=lambda state,runner:state
    tracking=Mock(id='pico',label='PICO');tracking.status.return_value={};tracking.preview_script.return_value=None
    runner=Mock()
    console=Console(backend,om/'onboardmimic/console/static',runner=runner,connection=connection,tracking=tracking)
    parent=start(stack,om_server(console))
    lab=start(stack,lab_server(client,embed_origins=[parent]))
    console.lab_embed=describe(lab+'/embed')
    report={'synthetic_sensors':True,'physical_motion':False,'om_root':str(om)}
    with sync_playwright() as p:
        options=dict(headless=True,args=['--no-sandbox'])
        if len(sys.argv)>3:options['executable_path']=sys.argv[3]
        browser=p.chromium.launch(**options)
        try:
            page=browser.new_page(viewport={'width':1280,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(parent)
            page.locator('#lab-embed-region iframe').wait_for()
            assert page.locator('iframe').count()==1
            assert page.locator('#collection-panel').count()==0
            frame=page.frame_locator('#lab-embed-region iframe')
            page.locator('#language').select_option('en')
            expect(frame.locator('#episode-start')).to_have_text('Record')
            page.locator('#language').select_option('zh-CN')
            expect(frame.locator('#episode-start')).to_have_text('开始数采')
            frame.locator('body').evaluate("""() => window.dispatchEvent(new MessageEvent('message', {
                source: parent, origin: 'http://untrusted.invalid',
                data: {type: 'onboardmimic.lab.language', version: 1, language: 'en'}
            }))""")
            expect(frame.locator('#episode-start')).to_have_text('开始数采')
            frame.locator('#tab-replay').click()
            expect(frame.locator('#collection-view')).to_be_hidden()
            expect(frame.locator('#replay-real')).to_be_disabled()
            page.locator('#language').select_option('en')
            expect(frame.locator('#replay-real')).to_have_text('Not ready')
            page.locator('#language').select_option('zh-CN')
            expect(frame.locator('#replay-real')).to_have_text('尚未就绪')
            frame.locator('#tab-collection').click()
            frame.locator('#episode-task').fill('OM iframe integration')
            frame.locator('#episode-start').click()
            frame.locator('#collection-state[data-state="recording"]').wait_for()
            episode=client.status()['episode_id']
            frame.locator('#tab-replay').click()
            expect(frame.locator('#tab-recording')).to_be_visible()
            assert client.status()['state']=='recording'
            frame.locator('#tab-collection').click()
            page.locator('#language').select_option('en')
            expect(frame.locator('#collection-state')).to_have_text('Recording')
            assert client.status()['episode_id']==episode
            page.locator('iframe').evaluate('(el)=>el.src=el.src')
            frame.locator('#collection-state[data-state="recording"]').wait_for()
            assert client.status()['episode_id']==episode and not collector.stop_event.is_set()
            page.reload()
            frame.locator('#collection-state[data-state="recording"]').wait_for()
            assert client.status()['episode_id']==episode
            expect(frame.locator('#collection-state')).to_have_text('Recording')
            frame.locator('#episode-stop').click()
            frame.locator('#collection-state[data-state="review"]').wait_for()
            frame.locator('#episode-save').click()
            frame.locator('#collection-state[data-state="saved"]').wait_for(timeout=20000)
            metadata,rows=read_aligned(output/'data/episodes'/episode)
            assert len(rows)==20
            token=page.request.get(lab+'/api/session').json()['token']
            denied=page.request.post(lab+'/api/collection',data={'action':'discard'},headers={'Origin':parent,'X-Control-Token':token})
            assert denied.status==403
            assert not errors,errors
            backend.command.assert_not_called();backend.control.assert_not_called();runner.assert_not_called()
            page.screenshot(path=str(output/'integrated-page.png'),full_page=True)
            report.update(language_switch_passed=True,untrusted_language_ignored=True,passed=True,saved_rows=len(rows),episode_id=episode,iframe_reload_preserved=True,
                parent_reload_preserved=True,parent_write_status=denied.status,motion_calls=0,page_errors=errors)
        finally:browser.close()
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
