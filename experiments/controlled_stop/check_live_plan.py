"""Run per-trial planning without sockets, services, or motor commands."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
from unittest.mock import patch
import sys
import time
import numpy as np
from worker import Experiment


def main(path,coordinated=False):
    frame=json.loads(Path(path).read_text())['raw_feedback']
    # A wrist change between trials must be incorporated, not matched to an old plan.
    frame['motor_state'][26]['q']+=.12
    for m in frame['motor_state'][:29]:m['dq']=0.
    with TemporaryDirectory() as directory:
        e=Experiment.__new__(Experiment)
        e.stop_event=SimpleNamespace(wait=lambda seconds:False)
        e.abort=threading.Event();e.lock=threading.Lock();e.generation=1
        e.controller=SimpleNamespace(phase='holding',trial_stage='measured_shallow' if coordinated else 'handoff_hold')
        e.frame=frame;e.captured=time.monotonic();e.shallow_plan=None;e.coordinated_plan=None
        records=[];e.recorder=SimpleNamespace(directory=Path(directory),submit=records.append)
        e.audit=lambda *args,**kwargs:None
        e.prepare_shallow(1,coordinated)
        state=e.coordinated_state if coordinated else e.plan_state
        error=e.coordinated_error if coordinated else e.plan_error
        plan=e.coordinated_plan if coordinated else e.shallow_plan
        assert state=='ready',error
        assert e.controller.phase=='holding'
        q=np.array([m['q'] for m in frame['motor_state'][:29]])
        np.testing.assert_allclose(plan['q'][0],q)
        offset=15 if coordinated else 12
        np.testing.assert_allclose(plan['q'][:,offset:],np.broadcast_to(q[offset:],plan['q'][:,offset:].shape))
        assert len(records)==1
        # Simulate stop/reset while the subprocess runs: the stale result must not publish.
        e.shallow_plan=None;e.coordinated_plan=None;e.captured=time.monotonic();e.plan_state='generating'
        class Interrupted:
            returncode=0
            def communicate(self,timeout):
                e.generation+=1;e.abort.set();return '', ''
        with patch('worker.subprocess.Popen',return_value=Interrupted()):e.prepare_shallow(1,coordinated)
        assert e.shallow_plan is None and e.coordinated_plan is None and len(records)==1
    print('Per-trial wrist change accepted; unaffected joints preserved; no automatic motion; stopped-trial result discarded; coordinated='+str(coordinated))


if __name__=='__main__':main(sys.argv[1],coordinated='--coordinated' in sys.argv[2:])
