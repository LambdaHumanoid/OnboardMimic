"""Task UI regression with local fixtures only, never robot endpoints."""
import sys
import tempfile
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from onboardmimic_lab.console import make_server
from onboardmimic_lab.tasks import Tasks


class Client:
    def __init__(self, root):
        self.tasks=Tasks(root);self.state='idle';self.task_id=None;self.actions=[];self.indices={};self.index=None;self.exports=[]
    def status(self):
        return dict(enabled=True,state=self.state,counts={},pending=[],exports=self.exports,camera=dict(ready=True),
                    tasks=self.tasks.list(),task_id=self.task_id,episode_index=self.index)
    def runtime_status(self):
        return dict(feedback_continuity=dict(ready=True),lab=dict(record_ready=True))
    def command(self, value):
        self.actions.append(value)
        if value['action']=='start':
            self.task_id=self.tasks.resolve(value)['id'];self.state='recording'
            self.index=self.indices.get(self.task_id,0);self.indices[self.task_id]=self.index+1
        elif value['action']=='stop':self.state='review'
        elif value['action']=='save':self.state='saved'
        return self.status()


class Replay:
    real=None
    def __init__(self, client):self.client=client;self.commands=[];self.ready=True
    def status(self):
        tasks=[dict(t,episode_count=2) for t in self.client.tasks.list()]
        return dict(configured=True,tasks=tasks,episodes=[dict(export_ready=self.ready,export=dict(state='complete' if self.ready else 'queued'),id=t['id']+'-'+str(i),task_id=t['id'],
            task=t['description'],episode_index=i,collector_name=('张三','李四')[i],frames=10,duration_s=.2) for t in tasks for i in range(2)])
    def command(self,value):
        assert value['action']=='inspect',value
        self.commands.append(value)
        return dict(verified=True,summary=dict(frames=10),segments=[])
    def close(self):pass
    def preview(self, episode):raise ValueError('Preview omitted in UI fixture')


with tempfile.TemporaryDirectory() as directory:
    client=Client(Path(directory));replay=Replay(client)
    server=make_server(client,replay=replay)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path=sys.argv[1],args=['--no-sandbox'])
            page=browser.new_page(locale='zh-CN');errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:'+str(server.server_port))
            expect(page.locator('#episode-start')).to_be_enabled()
            page.locator('#task-name').fill('杯子搬运');page.locator('#episode-task').fill('拿杯子');page.locator('#task-collector').fill('张三')
            page.locator('#episode-start').click()
            expect(page.locator('#collection-state')).to_have_attribute('data-state','recording')
            first=client.task_id
            expect(page.locator('#collection-id')).to_have_text('episode0')
            for _ in range(2):
                page.locator('#episode-stop').click();expect(page.locator('#episode-save')).to_be_enabled()
                page.locator('#episode-save').click();expect(page.locator('#episode-start')).to_be_enabled()
                if _==0:
                    expect(page.locator('#task-collector')).to_be_enabled()
                    page.locator('#task-collector').fill('李四')
                    page.locator('#episode-start').click()
                    expect(page.locator('#collection-state')).to_have_attribute('data-state','recording')
                    assert client.task_id==first and client.actions[-1]['task_id']==first
                    assert client.actions[-1]['collector_name']=='李四'
                    expect(page.locator('#collection-id')).to_have_text('episode1')
            page.reload();expect(page.locator('#collection-task')).to_have_value(first)
            expect(page.locator('#task-name')).to_have_value('杯子搬运')
            expect(page.locator('#task-collector')).to_have_value('')
            page.locator('#task-new').click()
            expect(page.locator('#task-name')).to_be_focused()
            expect(page.locator('#task-name')).to_have_value('')
            expect(page.locator('#episode-task')).to_have_value('');expect(page.locator('#task-collector')).to_have_value('')
            page.locator('#episode-start').click();expect(page.locator('#collection-state')).to_have_attribute('data-state','recording')
            second=client.task_id;assert first!=second
            expect(page.locator('#collection-id')).to_have_text('episode0')
            page.locator('#episode-stop').click();expect(page.locator('#episode-save')).to_be_enabled()
            page.locator('#episode-save').click();expect(page.locator('#episode-start')).to_be_enabled()
            client.exports=[dict(id='a'*32,episode_index=0,task_name='杯子搬运',state='paused')]
            expect(page.locator('#collection-exports')).to_contain_text('对齐已暂停')
            expect(page.locator('#episode-start')).to_be_enabled()
            client.exports[0].update(state='failed',error='missing source packet PTS')
            expect(page.locator('#collection-exports')).to_contain_text('对齐失败')
            page.locator('#collection-exports summary').click()
            page.wait_for_timeout(1200)
            assert page.locator('#collection-exports details').get_attribute('open') is not None
            page.get_by_role('button',name='重试对齐',exact=True).click()
            assert client.actions[-1]==dict(action='retry_export',id='a'*32)
            replay.ready=False
            page.locator('#tab-replay').click()
            expect(page.locator('#replay-task option')).to_have_count(3)
            page.locator('#replay-task').select_option(first)
            expect(page.locator('#replay-task option:checked')).to_contain_text('杯子搬运')
            expect(page.locator('#replay-episode option')).to_have_count(3)
            expect(page.locator('#replay-episode option').nth(1)).to_contain_text('episode0')
            page.locator('#replay-episode').select_option(first+'-0')
            expect(page.locator('#replay-details')).to_be_visible()
            expect(page.locator('#replay-validation-state')).to_contain_text('等待对齐')
            assert replay.commands==[]
            replay.ready=True
            expect(page.locator('#replay-validation-state')).to_have_text('',timeout=10000)
            assert replay.commands[-1]['action']=='inspect'
            expect(page.locator('#replay-task-person')).to_contain_text('张三')
            expect(page.locator('#replay-task')).to_be_enabled()
            page.locator('#replay-episode').select_option(first+'-1')
            expect(page.locator('#replay-task-person')).to_contain_text('李四')
            expect(page.locator('#replay-task')).to_be_enabled()
            page.locator('#replay-task').select_option(second)
            expect(page.locator('#replay-episode')).to_have_value('')
            expect(page.locator('#replay-details')).to_be_hidden()
            values=page.locator('#replay-episode option').evaluate_all('(nodes)=>nodes.map(x=>x.value)')
            assert values==['',second+'-0',second+'-1'],values
            english=browser.new_page(locale='en-US')
            english.goto('http://127.0.0.1:'+str(server.server_port))
            expect(english.locator('#collection-exports')).to_contain_text('Alignment failed')
            expect(english.get_by_role('button',name='Retry alignment',exact=True)).to_be_visible()
            expect(english.locator('#episode-start')).to_be_enabled()
            english.close()
            assert not errors,errors
            browser.close()
            print('PASS: multiple episodes per task, optional metadata, reload selection, replay task filtering; no robot commands.')
    finally:
        server.shutdown();server.server_close();thread.join()
