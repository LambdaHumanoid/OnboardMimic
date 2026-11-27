"""Temporary releases, fake systemd and loopback HTTP only; no robot access."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import urlopen
from onboardmimic.deployment.plan import impact,manifest,UNITS
from onboardmimic.deployment.activate import apply,atomic,guard
from onboardmimic.deployment.build import mirror,build
from onboardmimic.console.assets import select,pin


def release(root,files):
    root.mkdir(parents=True,exist_ok=True);entries={}
    for name,text in files.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        entries[name]=dict(sha256=hashlib.sha256(text.encode()).hexdigest(),executable=False)
    sid=hashlib.sha256(json.dumps(entries,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    m=dict(schema=1,source_id=sid,files=entries);(root/'source-manifest.json').write_text(json.dumps(m));return m


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.old=self.root/'old';self.new=self.root/'new';self.config=self.root/'config'
        self.files={'onboardmimic/console/static/style.css':'old','lab/src/onboardmimic_lab/static/replay-panel.html':'old panel',
                    'onboardmimic/core/runtime.py':'unchanged','lab/src/onboardmimic_lab/console.py':'unchanged'}
        self.before=release(self.old,self.files)

    def candidate(self,changes):
        return release(self.new,dict(self.files,**changes))

    def systemd(self,args,**kw):
        self.commands.append(args);command=args[2]
        if command=='show':
            text='LoadState=loaded\nActiveState=active\nWorkingDirectory='+str(self.old)+'\nMainPID=123\n'
        else:raise AssertionError('Assets must never mutate services: '+str(args))
        return SimpleNamespace(stdout=text,returncode=0)

    def test_css_hot_update_preserves_all_processes_and_pin(self):
        after=self.candidate({'onboardmimic/console/static/style.css':'new'})
        self.commands=[]
        def read(url,token=None):
            self.assertTrue(url.endswith('/api/deployment-assets'))
            if (self.config/'assets.json').exists():return dict(schema=1,active=after['source_id'])
            return dict(schema=1,active=None)
        result=apply(self.old,self.new,self.config,self.systemd,read)
        self.assertEqual(result['restarted'],[])
        self.assertEqual(set(result['untouched']),set(UNITS.values()))
        with patch.dict(os.environ,{'OM_ASSET_REGISTRY':str(self.config/'assets.json')}):
            current,version=select(self.old/'onboardmimic/console/static','onboardmimic/console/static')
            self.assertEqual((current/'style.css').read_text(),'new')
            old,_=select(current,'onboardmimic/console/static','/style.css?asset_version='+self.before['source_id'])
            self.assertEqual((old/'style.css').read_text(),'old')
            self.assertIn('asset_version='+version,pin('<link href="/style.css">',version))
            with self.assertRaises(ValueError):select(current,'onboardmimic/console/static','?asset_version=../../x')

    def test_scope_and_shared_native_header(self):
        for changes,services,native in [
            ({'lab/src/onboardmimic_lab/static/replay.js':'new'},set(),set()),
            ({'lab/docs/collection.md':'documentation only'},set(),set()),
            ({'lab/src/onboardmimic_lab/console.py':'new'},{'lab_console'},set()),
            ({'onboardmimic/console/assets.py':'new'},{'console','lab_console'},set()),
            ({'onboardmimic/robots/unitree_g1/connect_services.py':'new'},{'connect'},set()),
            ({'onboardmimic/backends/sonic/deploy/src/g1/g1_deploy_onnx_ref/include/native_consumption.hpp':'new'},None,{'sonic','bridge'}),
            ({'onboardmimic/robots/unitree_g1/native_bridge/protection_roll.hpp':'new'},None,{'bridge'})]:
            plan=impact(self.before,self.candidate(changes))
            if services is not None:self.assertEqual(set(plan['services']),services)
            self.assertEqual(set(plan['native']),native)

    def test_native_change_requires_idle_and_estop(self):
        plan=impact(self.before,self.candidate({'onboardmimic/robots/unitree_g1/native_bridge/protection_roll.hpp':'new'}))
        control=dict(state='active',pids=[1],execution=dict(state='completed'),protection_executor=dict(fresh=True,armed=True))
        with self.assertRaises(ValueError):guard(plan,control,dict(state='idle'),bridge_active=True)
        control.update(state='stopped',pids=[]);control['protection_executor'].update(armed=False,estopped=True)
        guard(plan,control,dict(state='idle'),bridge_active=True)
        control['execution']['state']='running'
        with self.assertRaises(ValueError):guard(plan,control,dict(state='idle'))

    def test_python_lab_restart_denied_during_capture_or_replay(self):
        plan=impact(self.before,self.candidate({'lab/src/onboardmimic_lab/console.py':'new'}))
        with self.assertRaises(ValueError):guard(plan,dict(execution=dict(state='running')),dict(state='idle'))
        with self.assertRaises(ValueError):guard(plan,dict(execution=dict(state='completed')),dict(state='recording'))
        guard(plan,dict(state='active',pids=[123],execution=dict(state='completed')),dict(state='idle'))

    def test_tamper_and_old_server_fail_before_mutation(self):
        self.candidate({'onboardmimic/console/static/style.css':'new'});self.commands=[]
        with self.assertRaises(ValueError):apply(self.old,self.new,self.config,self.systemd,lambda *a:dict(schema=0))
        self.assertFalse((self.config/'assets.json').exists())
        (self.new/'onboardmimic/console/static/style.css').write_text('tampered')
        with self.assertRaises(ValueError):manifest(self.new)

    def test_cache_mirror_preserves_unchanged_mtime_and_removes_old(self):
        target=self.root/'cache';mirror(self.old,target,self.before['files'],{})
        stable=target/'onboardmimic/core/runtime.py';stamp=stable.stat().st_mtime_ns
        after=self.candidate({'onboardmimic/console/static/style.css':'new'})
        mirror(self.new,target,after['files'],self.before['files'])
        self.assertEqual(stable.stat().st_mtime_ns,stamp)
        (self.new/'onboardmimic/core/runtime.py').chmod(0o755)
        mirror(self.new,target,after['files'],after['files'])
        self.assertTrue(stable.stat().st_mode&0o111)
        self.assertEqual(stable.stat().st_mtime_ns,stamp)
        self.assertEqual((target/'onboardmimic/console/static/style.css').read_text(),'new')
        reduced={n:v for n,v in after['files'].items() if n!='onboardmimic/console/static/style.css'}
        mirror(self.new,target,reduced,after['files'])
        self.assertFalse((target/'onboardmimic/console/static/style.css').exists())

    def prepare_service_switch(self):
        from onboardmimic.deployment.stage import stage
        self.candidate({'lab/src/onboardmimic_lab/console.py':'new python'})
        for n in ('.venv','.lab-venv'):(self.old/n).mkdir()
        binary_names=['onboardmimic/backends/sonic/deploy/target/release/g1_deploy_onnx_ref',
            'onboardmimic/robots/unitree_g1/native_bridge/build/sonic_command_bridge',
            'onboardmimic/robots/unitree_g1/native_bridge/build/sonic_state_reader']
        binaries={}
        for n in binary_names:
            p=self.old/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('not executable TEST ONLY')
            binaries[n]=dict(sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        (self.old/'native-build-verification.json').write_text(json.dumps(dict(source_id=self.before['source_id'],compiler='fixture',binaries=binaries)))
        (self.old/'device-artifacts.json').write_text(json.dumps(dict(assets={})))
        stage(self.old,self.new)
        self.home=self.root/'home';self.commands=[];self.active={u:True for u in UNITS.values()}
        for u in UNITS.values():
            p=self.home/'.config/systemd/user'/u;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text('[Service]\nWorkingDirectory='+str(self.old)+'\n')
        self.config.mkdir(exist_ok=True);(self.config/'http.token').write_text('fake-token')

    def mutable_systemd(self,args,**kw):
        self.commands.append(args);action=args[2];text=''
        if action=='show':
            unit=args[3];p=self.home/'.config/systemd/user'/unit
            root=p.read_text().split('WorkingDirectory=')[1].strip()
            text=root+'\n' if '--value' in args else 'LoadState=loaded\nActiveState='+('active' if self.active[unit] else 'inactive')+'\nWorkingDirectory='+root+'\nMainPID=123\n'
        elif action in ('start','stop'):self.active[args[3]]=action=='start'
        elif action!='daemon-reload':raise AssertionError(args)
        return SimpleNamespace(stdout=text,returncode=0)

    def service_read(self,url,token=None):
        if url.endswith('/api/controller'):return dict(state='active',pids=[42],execution=dict(state='completed'))
        if url.endswith('/api/collection'):return dict(state='idle')
        if url.endswith('/api/replay'):return dict(job=None)
        return dict(schema=1,active=json.loads((self.config/'assets.json').read_text())['active'])

    def test_lab_python_only_restarts_lab_console(self):
        self.prepare_service_switch()
        with patch('pathlib.Path.home',return_value=self.home):
            result=apply(self.old,self.new,self.config,self.mutable_systemd,self.service_read)
        self.assertEqual(result['restarted'],['onboardmimic-lab-console.service'])
        self.assertEqual([x[3] for x in self.commands if x[2]=='stop'],['onboardmimic-lab-console.service'])
        self.assertTrue(self.active['onboardmimic.service'])

    def test_connect_update_invalidates_ready_without_starting_motor_services(self):
        from onboardmimic.deployment.stage import stage
        self.prepare_service_switch()
        self.candidate({'onboardmimic/robots/unitree_g1/connect_services.py':'updated connector'})
        stage(self.old,self.new)
        def reader(url,token=None):
            if url.endswith('/api/controller'):
                return dict(state='stopped',pids=[],execution=dict(state='empty'),
                            protection_executor=dict(fresh=True,estopped=True))
            return self.service_read(url,token)
        with patch('pathlib.Path.home',return_value=self.home):
            result=apply(self.old,self.new,self.config,self.mutable_systemd,reader)
        self.assertEqual(result['restarted'],[])
        self.assertEqual([x[3] for x in self.commands if x[2]=='stop'],[UNITS['connect']])
        self.assertFalse(any(x[2]=='start' for x in self.commands))
        self.assertFalse(self.active[UNITS['connect']])
        self.assertTrue(self.active[UNITS['bridge']])
        self.assertIn(str(self.new),(self.home/'.config/systemd/user'/UNITS['connect']).read_text())

    def test_failed_activation_restores_registry_and_only_touched_service(self):
        self.prepare_service_switch()
        def reader(url,token=None):
            if url.endswith('/api/deployment-assets'):raise OSError('injected verification failure')
            return self.service_read(url,token)
        ticks=iter(range(0,10000,30))
        with patch('pathlib.Path.home',return_value=self.home),patch('onboardmimic.deployment.activate.time.monotonic',side_effect=lambda:next(ticks)):
            with self.assertRaises(OSError):apply(self.old,self.new,self.config,self.mutable_systemd,reader)
        self.assertFalse((self.config/'assets.json').exists())
        self.assertIn(str(self.old),(self.home/'.config/systemd/user/onboardmimic-lab-console.service').read_text())
        self.assertTrue(self.active['onboardmimic-lab-console.service'])
        self.assertTrue(all(x[3]=='onboardmimic-lab-console.service' for x in self.commands if x[2] in ('start','stop')))

    def test_real_cmake_cache_reuses_unchanged_object(self):
        import shutil
        compiler=shutil.which('g++')
        if not compiler or not shutil.which('cmake'):self.skipTest('C++ build tools unavailable')
        files={'toy/CMakeLists.txt':'cmake_minimum_required(VERSION 3.10)\nproject(cache_probe)\nadd_executable(probe main.cpp helper.cpp)\n',
               'toy/main.cpp':'extern int helper(); int main(){return helper();}\n',
               'toy/helper.cpp':'int helper(){return 0;}\n'}
        release(self.new,files)
        spec=dict(compiler=compiler,components={'bridge':dict(source='toy',configure=[],targets=['probe'],
            tests=[['{build}/probe']],binaries={'toy/bin/probe':'{build}/probe'})})
        first=build(self.new,self.root/'cache',spec,['bridge'])
        workspace=Path(first['cache_workspace']);obj=next(workspace.rglob('helper.cpp.o'));mtime=obj.stat().st_mtime_ns
        files['toy/main.cpp']='extern int helper(); volatile int v=0; int main(){return helper()+v;}\n'
        release(self.new,files)
        second=build(self.new,self.root/'cache',spec,['bridge'])
        self.assertEqual(first['cache_key'],second['cache_key'])
        self.assertEqual(mtime,obj.stat().st_mtime_ns)
        self.assertNotEqual(first['binaries']['toy/bin/probe']['sha256'],second['binaries']['toy/bin/probe']['sha256'])
        self.assertNotEqual(first['source_id'],second['source_id'])
