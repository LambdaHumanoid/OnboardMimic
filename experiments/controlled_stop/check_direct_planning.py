"""Offline workflow test: preparing a plan must never stop/claim SONIC."""
import json,shutil,sys,threading,time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from worker import Experiment
from direct_controller import DirectTrajectory
from native_model import NativeModel


def main(source,plan):
    frame=json.loads(Path(source).read_text())['raw_feedback']
    for stale in (False,True):
        with TemporaryDirectory() as tmp:
            e=Experiment.__new__(Experiment);e.lock=threading.Lock();e.stop_event=threading.Event();e.abort=threading.Event()
            e.generation=1;e.phase='idle';e.frame=frame;e.model=NativeModel(Path(__file__).with_name('model.json'))
            e.direct_trajectory_class=DirectTrajectory;e.direct_plan=None;e.direct_template=None;e.direct_state='generating'
            calls=[]
            def status():e.captured=time.monotonic();return dict(state='active',input_mode='planner',stationary=True)
            e.lifecycle=SimpleNamespace(status=status,action=lambda name:calls.append(name));e.audit=lambda *args,**kwargs:None
            class Process:
                returncode=0
                def __init__(self,cmd,**kwargs):shutil.copyfile(plan,cmd[-1])
                def communicate(self,timeout):
                    if stale:e.generation+=1;e.abort.set()
                    return '', ''
            with patch('worker.ROOT',Path(tmp)),patch('worker.subprocess.Popen',Process),patch('worker.ipc',side_effect=AssertionError('Planning requested bridge control')):
                e.prepare_direct(1)
            assert calls==[] and e.phase=='idle'
            if stale:assert e.direct_plan is None and e.direct_template is None
            else:assert e.direct_state=='ready' and e.direct_template is not None
    print('Planning does not claim control or stop SONIC; canceled-generation results are discarded')
    for owned in (True,False):
        e=Experiment.__new__(Experiment);e.lock=threading.Lock();e.abort=threading.Event();e.generation=1
        e.stream=42;e.socket_path=Path('/nonexistent-test-socket');e.confirmed_handoff=False;e.shallow_enabled=False;e.controller=object()
        calls=[];e.audit=lambda *args,**kwargs:None
        def action(name):
            assert e.confirmed_handoff
            calls.append(name)
        e.lifecycle=SimpleNamespace(action=action)
        def fake_ipc(path,action,**kwargs):return dict(stream=42 if owned else 41)
        with patch('worker.ipc',fake_ipc):e.confirm_handoff(1)
        if owned:assert calls==['estop'] and e.confirmed_handoff
        else:assert calls==[] and e.abort.is_set() and e.phase=='fault'
    print('SONIC stop requested only after bridge confirms ownership; failed handoff never stops SONIC')


if __name__=='__main__':main(*sys.argv[1:])
