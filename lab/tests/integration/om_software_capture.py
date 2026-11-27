"""Opt-in actual OM software server + OML capture. No robot or production services.
Usage: python tests/integration/om_software_capture.py OM_ROOT NEW_OUTPUT
"""
import json
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError

from onboardmimic_lab.collection import Collector, load_config
from onboardmimic_lab.runtime_status import RuntimeStatus
from onboardmimic_lab.dataset import read_aligned
from onboardmimic_lab.reference_replay import prepare_plan, summary
from onboardmimic_lab.video_timing import MpegTsPtsExtractor
from onboardmimic.backends.sonic.native.replay_plan import validate as validate_physical
from onboardmimic_lab.simulation import run_simulation

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_collection import make_video
from export_test_helpers import wait_export


def port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET,kind) as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


def wait(fn, seconds=10):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        result=fn()
        if result:return result
        time.sleep(.01)
    raise RuntimeError('timed out waiting for integration state')


def exercise(om,out,dim):
    out.mkdir();http,telemetry,camera=port(),port(),port(socket.SOCK_DGRAM)
    token=secrets.token_hex(32);tokenfile=out/'token';tokenfile.write_text(token);tokenfile.chmod(0o600)
    parameters=dict(joint_names=['joint_'+str(i) for i in range(dim)],period_ns=20000000,
                    max_gap_ns=60000000,watchdog_ns=200000000,speed_rad_s=1.,limit_rad=2.)
    (out/'parameters.json').write_text(json.dumps(parameters))
    configfile=out/'exported.collection.json';log=(out/'server.log').open('w')
    proc=subprocess.Popen([sys.executable,'-m','onboardmimic.core.execution.server','--token-file',str(tokenfile),
        '--config',str(out/'parameters.json'),'--export-config',str(configfile),'--port',str(http),
        '--telemetry-endpoint','tcp://127.0.0.1:'+str(telemetry)],cwd=om,stdout=log,stderr=log)
    stop=threading.Event();threads=[];collector=None;errors=[];seq=0
    opener=build_opener(ProxyHandler({}))
    def request(route,payload=None):
        body=None if payload is None else json.dumps(payload).encode()
        with opener.open(Request('http://127.0.0.1:'+str(http)+route,data=body,
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}),timeout=2) as r:return json.load(r)
    def command(action,**extra):
        nonlocal seq
        status=request('/api/controller');seq+=1
        payload=dict(action=action,epoch=status['epoch'],ticket=status['ticket'],client='oml-integration',seq=seq,
                     session_id=status['lab']['session_id'],contract_sha256=status['lab']['contract_sha256'],**extra)
        return request('/api/execution/v1',payload)
    def state(name):
        s=collector.status()
        if s['state']=='failed':raise RuntimeError(s['error'])
        return s if s['state']==name else None
    try:
        wait(lambda:configfile.exists() or (proc.poll() is not None and (_ for _ in ()).throw(RuntimeError('OM exited'))))
        config=json.loads(configfile.read_text());config.update(root=str(out/'data'),gateway='http://127.0.0.1:'+str(http),
                         token_file=str(tokenfile),camera_port=camera,max_duration_s=2)
        configfile.write_text(json.dumps(config,indent=2));config=load_config(configfile)
        capabilities=request('/api/execution/v1/capabilities')
        assert capabilities['hardware'] is False and capabilities['controller_spec']==config['controller_spec']
        command('start',mode='teleoperation')
        def references():
            i=0
            try:
                while not stop.is_set():
                    try:command('reference',reference_id='ref-'+str(i),reference={'q_target':[.1]*dim});i+=1
                    except HTTPError as e:
                        message=json.load(e)
                        if 'Queue full' not in str(message):raise RuntimeError(str(message))
                    stop.wait(.008)
            except Exception as e:errors.append(str(e))
        threads.append(threading.Thread(target=references));threads[-1].start()
        runtime=RuntimeStatus(config);wait(lambda:runtime()['lab']['record_ready'])
        video=make_video(out/'fixture.ts',frames=300);groups=[];group=bytearray()
        for offset in range(0,len(video),188):
            packet=video[offset:offset+188]
            if MpegTsPtsExtractor._packet_pts(packet) is not None and group:groups.append(bytes(group));group.clear()
            group.extend(packet)
        if group:groups.append(bytes(group))
        collector=Collector(config,runtime);collector.command(dict(action='start',task='OM software '+str(dim)+' joints'))
        def stream():
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
                for group in groups:
                    if stop.is_set():break
                    for offset in range(0,len(group),1316):udp.sendto(group[offset:offset+1316],('127.0.0.1',camera))
                    stop.wait(.02)
        threads.append(threading.Thread(target=stream));threads[-1].start()
        wait(lambda:state('recording'));time.sleep(2.2)
        assert state('recording')
        collector.command(dict(action='stop'));review=wait(lambda:state('review'))
        collector.command(dict(action='save'));wait(lambda:state('saved'))
        episode=out/'data/episodes'/review['episode_id'];wait_export(episode)
        metadata,rows=read_aligned(episode)
        assert len(rows)>20 and all(len(r['sample']['reference']['q_target'])==dim for r in rows)
        assert all(r['sample']['reference_evidence']=='consumed' for r in rows)
        # Pick a continuous raw segment explicitly if the OS scheduler introduced gaps.
        from onboardmimic_lab.reference_dataset import samples
        segments={}
        for row in samples(episode,config['controller_spec']):segments[row['segment_index']]=segments.get(row['segment_index'],0)+1
        plan=prepare_plan(episode,max(segments,key=segments.get))
        try:validate_physical(plan)
        except ValueError:pass
        else:raise AssertionError('generic physical preflight unexpectedly enabled')
        try:run_simulation(plan,config,out/'simulation')
        except ValueError as e:assert 'no simulation adapter' in str(e)
        else:raise AssertionError('software fixture treated as physical simulation')
        stop.set()
        for t in threads:t.join(3)
        wait(lambda:not runtime()['lab']['record_ready'])
        stopped=wait(lambda:request('/api/controller')['stop_ack'])
        assert not errors,errors
        return dict(joints=dim,rows=len(rows),plan=summary(plan),readiness_revoked=True,stop_ack=stopped,
                    physical_execution=False,software_only=True)
    finally:
        stop.set()
        for t in threads:t.join(3)
        if collector:collector.close()
        proc.terminate()
        try:proc.wait(5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
        log.close();tokenfile.unlink(missing_ok=True)


if __name__=='__main__':
    om=Path(sys.argv[1]).resolve();out=Path(sys.argv[2]).resolve();out.mkdir(exist_ok=False,parents=True)
    results=[exercise(om,out/str(dim),dim) for dim in (3,7)]
    (out/'result.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
