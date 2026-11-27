"""Actual Python/native replay protocol, CPU only. No motor interfaces linked."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from onboardmimic.backends.sonic.native.replay import NativeClient


def probe(binary, renew):
    with tempfile.TemporaryDirectory(prefix='om-replay-probe-') as folder:
        path=Path(folder)/'control.sock'
        with (Path(folder)/'out').open('w+') as output:
            process=subprocess.Popen([binary,str(path)],stdout=output)
            try:
                deadline=time.monotonic()+3
                while not path.exists():
                    if time.monotonic()>deadline:raise RuntimeError('Native socket unavailable')
                    time.sleep(.01)
                client=NativeClient(path,process.pid)
                try:NativeClient(path,process.pid+100000).request('status')
                except RuntimeError:pass
                else:raise AssertionError('Wrong kernel peer identity accepted')
                plan=dict(entry_protocol=3,period_ns=20000000,artifacts={},start_q=[0.]*29,start_gravity=[0.,0.,-1.],frames=[dict(offset_ns=i*20000000,token=[float(i)]*64) for i in range(5)])
                plan['padding']=' '*(17*1024*1024)  # Exercise the former native 16 MiB cap.
                assert client.request('load',**plan)['state']=='loaded'
                time.sleep(.1);assert process.poll() is None
                assert client.request('start',run=123)['state']=='starting'
                sequence=0;until=time.monotonic()+8;finishing=False
                while process.poll() is None and time.monotonic()<until:
                    if renew:
                        sequence+=1
                        try:
                            state=client.request('renew',run=123,sequence=sequence)
                            output.flush();output.seek(0);rows=output.read().splitlines()
                            if not finishing and any(json.loads(row).get('frame')==4 for row in rows):
                                client.request('finish',run=123);finishing=True
                            if state.get('return_ready_ns'):client.request('release',run=123)

                        except (RuntimeError,OSError):
                            if process.poll() is None:time.sleep(.05)
                    time.sleep(.05)
                process.wait(timeout=1);assert process.returncode==0
                output.seek(0);lines=output.read().splitlines();assert lines[-1]==('PLANNER_HANDOFF' if renew else 'STOPPED_OUTPUT')
                ticks=[json.loads(line) for line in lines[:-1]]
                if renew:
                    assert set(t['frame'] for t in ticks)==set(range(5)),ticks
                    assert all(t['run']==123 and t['token0']==float(t['frame']) for t in ticks)
                else:assert not ticks, ticks
                return dict(renew=renew,frames=sorted(set(t['frame'] for t in ticks)),independent_stop=not renew,planner_handoff=renew,physical=False)
            finally:
                if process.poll() is None:process.kill();process.wait()


if __name__=='__main__':print(json.dumps([probe(sys.argv[1],True),probe(sys.argv[1],False)]))
