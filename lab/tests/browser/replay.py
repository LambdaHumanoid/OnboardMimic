"""Browser -> real native dataset -> CLI ONNX/MuJoCo -> video/report.
Usage: PYTHONPATH=src python tests/browser/replay.py DATA_ROOT SIMULATION_CONFIG OUTPUT
Uses explicitly synthetic native smoke data; never connects to a robot.
"""
import json
from pathlib import Path
import sys
import threading
from playwright.sync_api import sync_playwright, expect
from onboardmimic_lab.console import make_server
from onboardmimic_lab.replay_console import ReplayConsole

class IdleCollector:
    def status(self):return dict(enabled=True,state='idle',pending=[],counts={},elapsed_s=0,error='')
    def runtime_status(self):return dict(state='stopped',input_mode='pose',lab=dict(record_ready=False))
    def command(self,_):raise AssertionError('Replay UI must not issue collection or robot commands')

def main():
    out=Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True)
    replay=ReplayConsole(sys.argv[1],sys.argv[2],out/'jobs')
    server=make_server(IdleCollector(),replay=replay);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    report=dict(physical_motion=False,errors=[])
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox','--no-proxy-server'])
            page=browser.new_page(viewport=dict(width=1050,height=1000),locale='en-US')
            page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.goto('http://127.0.0.1:'+str(server.server_port)+'/embed')
            expect(page.locator('#collection-state')).to_have_attribute('data-state','idle')
            page.locator('#tab-replay').click();expect(page.locator('#collection-view')).to_be_hidden()
            expect(page.locator('#replay-episode option').nth(1)).to_be_attached()
            page.locator('#replay-episode').select_option(index=1)
            expect(page.locator('#replay-simulate')).to_be_disabled()
            page.locator('#replay-inspect').click();expect(page.locator('#replay-validation-state')).to_contain_text('Validated',timeout=30000)
            expect(page.locator('#replay-real')).to_be_disabled()
            page.locator('#replay-simulate').click();expect(page.locator('#replay-job-state')).to_have_text('Simulation completed',timeout=120000)
            expect(page.locator('#replay-video')).to_be_visible()
            expect(page.locator('#replay-video')).to_have_js_property('readyState',4)
            report['video_duration']=page.locator('#replay-video').evaluate('(v)=>v.duration')
            page.screenshot(path=str(out/'replay-desktop.png'),full_page=True)
            page.set_viewport_size(dict(width=390,height=950));page.screenshot(path=str(out/'replay-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.reload();page.locator('#tab-replay').click();expect(page.locator('#replay-job-state')).to_have_text('Simulation completed')
            page.locator('#tab-collection').click();expect(page.locator('#collection-state')).to_have_attribute('data-state','idle')
            assert not report['errors'],report
            report.update(passed=True,job=replay.status()['job']);browser.close()
    finally:server.shutdown();server.server_close();thread.join();replay.close()
    (out/'result.json').write_text(json.dumps(report,indent=2));print(json.dumps({'passed':report['passed'],'video_duration':report['video_duration'],'errors':report['errors']}))

if __name__=='__main__':main()
