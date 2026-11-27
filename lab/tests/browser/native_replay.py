"""Actual OM HTTP + OML UI, with OM's isolated fake-process/writer fixture.
Usage: PYTHONPATH=src python tests/browser/native_replay.py OM_ROOT NEW_OUTPUT
All provenance is a unit substitution. No robot endpoint or native process is used.
"""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import sys
import threading
from playwright.sync_api import sync_playwright, expect
from onboardmimic_lab.native_replay_client import NativeReplayClient, NativeReplay
from onboardmimic_lab.replay_console import ReplayConsole
from onboardmimic_lab.console import make_server as lab_server
from onboardmimic_lab.reference_dataset import RawEpisode
from onboardmimic_lab.dataset import export_episode, digest, write_json
from onboardmimic_lab.video_timing import MpegTsPtsExtractor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_collection import make_video
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from tests.sonic.test_reference_replay import ReplayLifecycleTests
from onboardmimic.core.gateway import make_server as om_server

class IdleCollector:
    def status(self):return dict(enabled=True,state='idle',counts={},pending=[],elapsed_s=0,error='')
    def runtime_status(self):return dict(state='active',input_mode='planner',lab=dict(record_ready=False))
    def command(self,_):raise AssertionError('No robot/collection commands allowed')

def serve(stack,server):
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def close():server.shutdown();server.server_close();thread.join()
    stack.callback(close)
    return 'http://127.0.0.1:'+str(server.server_port)

def scenario(p,out,mode):
    out.mkdir(parents=True);fixture=ReplayLifecycleTests();fixture.setUp()
    fixture.g.native_consumption.status=lambda *args:dict(record_ready=False)
    episode=out/'data/episodes/unit-test-only'
    raw=RawEpisode(episode,dict(controller_spec=fixture.plan['controller_spec'],task='UNIT TEST — no physical recording'))
    video=make_video(out/'input.ts',frames=3);raw.video(video)
    for frame,pts in zip(fixture.plan['frames'],MpegTsPtsExtractor().feed(video)):
        s=frame['sample'];raw.controller(s,s['timestamp_monotonic_ns']+2_000_000)
        raw.append('camera.jsonl',dict(sequence=s['index'],pts_90khz=pts,monotonic_ns=s['timestamp_monotonic_ns']-1_000_000))
    raw.finish();export_episode(episode)
    m=json.loads((episode/'manifest.json').read_text());m.update(status='saved',aligned_manifest=digest(episode/'aligned/episode.json'));write_json(episode/'manifest.json',m)
    calls=[];original=fixture.g.request
    def request(route,payload):calls.append(route);return original(route,payload)
    fixture.g.request=request
    with ExitStack() as stack:
        gateway=serve(stack,om_server(fixture.g,'t'*32))
        native=NativeReplay(NativeReplayClient(gateway,'t'*32))
        replay=ReplayConsole(out/'data',real=native)
        url=serve(stack,lab_server(IdleCollector(),replay=replay))
        browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox','--no-proxy-server']);stack.callback(browser.close)
        page=browser.new_page(viewport=dict(width=1100,height=1000),locale='zh-CN');errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url+'/embed');page.locator('#tab-replay').click()
        expect(page.locator('#replay-episode option').nth(1)).to_be_attached()
        page.locator('#replay-episode').select_option('unit-test-only');page.locator('#replay-inspect').click()
        expect(page.locator('#replay-real')).to_be_enabled();page.locator('#replay-real').click()
        expect(page.locator('#replay-real-start')).to_be_enabled()
        assert fixture.proc.kills==0;fixture.client.request.assert_not_called()
        if mode=='stop':
            fixture.g.latest['rt/lowstate'][1]['motor_state']=[dict(q=1.,dq=0.,motorstate=0)]*29
            page.locator('#replay-real-start').click()
            expect(page.locator('#replay-real-reason')).to_contain_text('Recorded start pose differs')
            assert fixture.proc.kills==0
            fixture.g.latest['rt/lowstate'][1]['motor_state']=[dict(q=0.,dq=0.,motorstate=0)]*29
            expect(page.locator('#replay-real-start')).to_be_enabled()
        page.locator('#replay-real-start').click()
        expect(page.locator('#replay-real-stop')).to_be_enabled();fixture.r.step()
        page.wait_for_timeout(400)
        assert any(route.endswith('/renew') for route in calls),calls
        sample=deepcopy(fixture.plan['frames'][0]['sample']);sample['replay']=dict(run=fixture.r.run,frame=0,scheduled_frame=True)
        sample['timestamp_monotonic_ns']=int(fixture.now*1e9);fixture.join.last=sample;fixture.r.step()
        expect(page.locator('#replay-real-progress')).to_contain_text('1/3')
        if mode=='stop':page.locator('#replay-real-stop').click()
        elif mode=='complete':
            for i in (1,2):sample['replay']['frame']=i;fixture.r.step()
        else:
            page.reload();page.locator('#tab-replay').click();page.wait_for_timeout(250)
            renews=sum(route.endswith('/renew') for route in calls)
            page.wait_for_timeout(400);assert sum(route.endswith('/renew') for route in calls)==renews
            fixture.now+=.6;fixture.r.step();assert fixture.r.reason=='lease_expired'
        expect(page.locator('#replay-real-state')).to_have_text('保护退出中，等待停止确认')
        expect(page.locator('#replay-real-ack')).to_have_text('停止尚未确认。')
        fixture.p.update(fenced=True,phase=2);fixture.r.step()
        expect(page.locator('#replay-real-ack')).to_contain_text('停止已确认')
        expected={'stop':'stopped','complete':'completed','disconnect':'failed'}[mode]
        assert native.snapshot()['status']['state']==expected,native.snapshot()
        assert fixture.proc.kills==1;fixture.g.protection_executor.cancel.assert_not_called()
        assert sum(route.endswith('/start') for route in calls)==(2 if mode=='stop' else 1)
        assert not errors,errors
        if mode=='complete':expect(page.locator('#replay-real-progress')).to_contain_text('3/3')
        page.screenshot(path=str(out/'page.png'),full_page=True)
        result=dict(passed=True,scenario=mode,synthetic_provenance=True,physical_motion=False,status=native.snapshot(),routes=calls,page_errors=errors)
        assert 'ticket' not in json.dumps(result) and 'epoch' not in json.dumps(result)
        (out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
        return dict(scenario=mode,passed=True,renew_requests=sum(route.endswith('/renew') for route in calls))

out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=False)
with sync_playwright() as p:results=[scenario(p,out/mode,mode) for mode in ('stop','complete','disconnect')]
(out/'result.json').write_text(json.dumps(dict(physical_motion=False,results=results),indent=2));print(json.dumps(results))
