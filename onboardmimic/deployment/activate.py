"""Atomic assets and selective service switch. Never starts a controller/bridge."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
from .plan import manifest, impact, UNITS, digest


def atomic(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            f.write(value);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def request(url, token=None):
    headers={'Authorization':'Bearer '+token} if token else {}
    return json.load(urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=5))


def guard(plan, controller, collection=None, simulation=None, bridge_active=False):
    if 'console' in plan.get('services',[]) and controller.get('lease_remaining_ms',0)>0:
        raise ValueError('Release active operator control before console restart')
    if plan['requires_replay_idle'] and controller.get('execution',{}).get('state') not in ('empty','loaded','completed','stopped','failed'):
        raise ValueError('Replay must be idle before service restart')
    if plan['requires_capture_idle']:
        if not collection or collection.get('state') not in ('idle','failed','review','saved','discarded'):
            raise ValueError('Capture must be idle before service restart')
        if simulation and simulation.get('state') in ('running','stopping'):raise ValueError('Simulation still active')
    if plan['requires_control_stopped']:
        p=controller.get('protection_executor',{})
        if controller.get('state')!='stopped' or controller.get('pids') or p.get('armed') or p.get('watching') or p.get('phase')==1:
            raise ValueError('Stop control with reliable physical support first')
        if bridge_active and not (p.get('fresh') and p.get('estopped')):raise ValueError('Bridge estop not confirmed')


def registry_update(previous, current, candidate, before, after):
    data=json.loads(previous) if previous else dict(schema=1,releases={})
    if data.get('schema')!=1:raise ValueError('Unsupported asset registry')
    data['releases'].update({before['source_id']:str(current),after['source_id']:str(candidate)})
    data['active']=after['source_id']
    return json.dumps(data,indent=2)+'\n'


def apply(current, candidate, config=None, runner=subprocess.run, read=request):
    current=Path(current).resolve();candidate=Path(candidate).resolve()
    config=Path(config or Path.home()/'.config/onboardmimic');config.mkdir(parents=True,exist_ok=True)
    with (config/'deployment.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before,after=manifest(current),manifest(candidate);plan=impact(before,after)
        registry=config/'assets.json';previous=registry.read_text() if registry.exists() else None
        if previous and json.loads(previous)['active']!=before['source_id']:raise ValueError('Deployment baseline changed')
        def ctl(*args,check=True):
            return runner(['systemctl','--user',*args],capture_output=True,text=True,check=check)
        units_dir=Path.home()/'.config/systemd/user'
        states={};changes={};actual_services=set(plan['services']);manifests={current:before,candidate:after}
        # Check the actual source of every loaded component, even after several
        # asset-only releases left its process on an older but equivalent snapshot.
        for component,unit in UNITS.items():
            result=ctl('show',unit,'-p','LoadState','-p','ActiveState','-p','WorkingDirectory','-p','MainPID')
            values=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
            if values.get('LoadState')=='not-found':continue
            if values.get('LoadState')!='loaded':raise ValueError('Unknown unit '+unit)
            root=Path(values['WorkingDirectory']).resolve()
            if root not in manifests:manifests[root]=manifest(root)
            m=manifests[root]
            if component in impact(m,after)['services']:actual_services.add(component)
            states[component]=dict(values,root=root)
        plan['services']=sorted(actual_services)
        plan['requires_control_stopped']=bool(actual_services-{'console','lab_console','collector'})
        plan['requires_capture_idle']=bool(actual_services & {'lab_console','collector'})
        plan['requires_replay_idle']=bool(actual_services)
        if actual_services-set(states):raise ValueError('Affected service missing from installation')
        if actual_services:
            report=json.loads((candidate/'native-build-verification.json').read_text())
            if report['source_id']!=after['source_id']:raise ValueError('Build source mismatch')
            if report.get('pending_components'):raise ValueError('Native build still pending')
            required={'onboardmimic/backends/sonic/deploy/target/release/g1_deploy_onnx_ref','onboardmimic/robots/unitree_g1/native_bridge/build/sonic_command_bridge','onboardmimic/robots/unitree_g1/native_bridge/build/sonic_state_reader'}
            if not required.issubset(report['binaries']):raise ValueError('Incomplete native build outputs')
            for n,h in report.get('dependencies',{}).items():
                if digest(candidate/n)!=h:raise ValueError('Native dependency changed: '+n)
            assets=json.loads((candidate/'device-artifacts.json').read_text())
            for n,row in assets['assets'].items():
                if digest(candidate/n)!=row['sha256']:raise ValueError('Model asset changed: '+n)
            for n,row in report['binaries'].items():
                if digest(candidate/n)!=row['sha256']:raise ValueError('Native binary mismatch: '+n)
        # Complete candidate with unchanged, verified runtime artifacts for Python
        # relocations. Native rebuilds must already have installed their outputs.
        if actual_services:
            for link in ('.venv','.lab-venv'):
                if not (candidate/link).exists():raise ValueError('Stage runtime environments before activation')
            for component in actual_services:
                state=states[component];unit=UNITS[component];old=str(state['root'])
                files=[units_dir/unit]+list((units_dir/(unit+'.d')).glob('*.conf'))
                matched=False
                for p in files:
                    if p.exists() and old in p.read_text():
                        text=p.read_text();changes[p]=(text,text.replace(old,str(candidate)));matched=True
                if not matched:raise ValueError('Unsupported unit path layout: '+unit)
        token=(config/'http.token').read_text().strip() if actual_services else None
        def check_idle():
            if not actual_services:return
            control=read('http://127.0.0.1:18921/api/controller',token)
            collection=read('http://127.0.0.1:8773/api/collection') if plan['requires_capture_idle'] else None
            sim=read('http://127.0.0.1:8773/api/replay').get('job') if plan['requires_capture_idle'] else None
            guard(plan,control,collection,sim,states.get('bridge',{}).get('ActiveState')=='active')
        check_idle()
        # An old server cannot honor the pointer: bootstrap by restarting only
        # the changed web Python services, never silently claim a hot update.
        if not actual_services:
            for port in (8769,8773):
                if read('http://127.0.0.1:'+str(port)+'/api/deployment-assets').get('schema')!=1:
                    raise ValueError('Install versioned asset support once before hot updates')
        backup=candidate/'deployment-backup.json'
        atomic(backup,json.dumps(dict(registry=previous,files={str(p):v[0] for p,v in changes.items()},plan=plan),indent=2))
        stopped=[];started=[]
        # web -> capture -> runtime, never restart controller/bridge automatically.
        order=['console','lab_console','collector','connect','bridge','primary','standby','relay','runtime']
        restart=[c for c in reversed(order) if c in actual_services and states[c]['ActiveState']=='active'
                 and c in ('runtime','console','lab_console','collector')]
        try:
            check_idle()
            for component in order:
                if component in actual_services and states[component]['ActiveState']=='active':
                    ctl('stop',UNITS[component]);stopped.append(component)
            for p,(_,new) in changes.items():atomic(p,new)
            if changes:ctl('daemon-reload')
            atomic(registry,registry_update(previous,current,candidate,before,after))
            for component in restart:
                ctl('start',UNITS[component]);started.append(component)
            for component in actual_services:
                wd=ctl('show',UNITS[component],'-p','WorkingDirectory','--value').stdout.strip()
                if Path(wd).resolve()!=candidate:raise ValueError('Service source switch failed')
            deadline=time.monotonic()+20
            while True:
                try:
                    for port in (8769,8773):
                        if read('http://127.0.0.1:'+str(port)+'/api/deployment-assets').get('active')!=after['source_id']:
                            raise ValueError('Asset publication unconfirmed')
                    break
                except Exception:
                    if time.monotonic()>deadline:raise
                    time.sleep(.2)
            manifest(candidate)
            result=dict(plan=plan,source_id=after['source_id'],restarted=[UNITS[x] for x in restart],
                        untouched=[UNITS[x] for x in states if x not in actual_services],
                        component_sources={UNITS[x]:after['source_id'] if x in actual_services else manifests[states[x]['root']]['source_id'] for x in states},physical_motion=False)
            atomic(config/'deployment-state.json',json.dumps(result,indent=2))
            atomic(candidate/'deployment-result.json',json.dumps(result,indent=2))
            return result
        except BaseException:
            for c in started:ctl('stop',UNITS[c],check=False)
            for p,(old,_) in changes.items():atomic(p,old)
            if previous is None:registry.unlink(missing_ok=True)
            else:atomic(registry,previous)
            if changes:ctl('daemon-reload')
            # Never restore motor writers after a failed deployment.
            for c in restart:
                if c in stopped:ctl('start',UNITS[c],check=False)
            raise
