"""GPU-free regression coverage for TensorRT 8/10 dispatch and buffer types."""
import contextlib
import ctypes
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]

class TensorRTCompatibilityTests(unittest.TestCase):
    def model(self, major):
        buffers = []
        def allocate(target, size):
            buffer = ctypes.create_string_buffer(size)
            buffers.append(buffer)
            target._obj.value = ctypes.addressof(buffer)
            return 0
        def ok(*args):
            return 0
        names = ['mode', 'output']
        context = SimpleNamespace(calls=[], addresses={})
        def execute(*args, **kwargs):
            context.calls.append((args, kwargs))
            return True
        if major == 10:
            def address(name, value):
                context.addresses[name] = value
                return True
            context.get_tensor_shape = lambda name: (1,)
            context.set_tensor_address = address
            context.execute_async_v3 = execute
            engine = SimpleNamespace(num_io_tensors=2, get_tensor_name=lambda i: names[i],
                get_tensor_dtype=lambda n: 'int64' if n == 'mode' else 'float32',
                get_tensor_mode=lambda n: 'input' if n == 'mode' else 'output')
        else:
            context.get_binding_shape = lambda i: (1,)
            context.execute_async_v2 = execute
            engine = SimpleNamespace(num_bindings=2, get_binding_name=lambda i: names[i],
                get_binding_dtype=lambda i: 'int32' if i == 0 else 'float32', binding_is_input=lambda i: i == 0)
        engine.create_execution_context = lambda: context
        class Logger:
            WARNING = 1
            def __init__(self, level): pass
        trt = SimpleNamespace(__version__=str(major)+'.0', Logger=Logger, nptype=lambda t: t,
            TensorIOMode=SimpleNamespace(INPUT='input'),
            Runtime=lambda logger: SimpleNamespace(deserialize_cuda_engine=lambda data: engine))
        class Library:
            def __getattr__(self, name):
                return allocate if name in ('cudaMalloc', 'cudaMallocHost') else ok
        path = ROOT/'onboardmimic/backends/sonic/deploy/onboard/benchmark.py'
        spec = importlib.util.spec_from_file_location('benchmark_test', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', tensorrt=trt), patch('ctypes.CDLL', return_value=Library()):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            engine_path = Path(tmp)/'model.engine'
            engine_path.write_bytes(b'mocked engine')
            model = module.Model(engine_path)
            model.run()
            self.assertEqual(model.inputs, ['mode'])
            self.assertEqual(model.outputs, ['output'])
            self.assertEqual(model.arrays['mode'].dtype, np.dtype('int64' if major == 10 else 'int32'))
            self.assertGreaterEqual(len(context.calls), 2)
            if major == 10:
                self.assertEqual(set(context.addresses), set(names))
                self.assertTrue(all('stream_handle' in kwargs for _, kwargs in context.calls))
            else:
                self.assertTrue(all(len(args[0]) == 2 for args, _ in context.calls))
    def test_trt8_retains_binding_api_and_int32(self): self.model(8)
    def test_trt10_uses_tensor_api_and_int64(self): self.model(10)

class SourceValidationTests(unittest.TestCase):
    def facts(self):
        return dict(arch='aarch64', ubuntu='22.04', l4t='36.4.3', tensorrt='10.3.0', cuda_runtime=12060,
                    board='NVIDIA Jetson Orin NX', memory_kib=16*1024*1024, gpu='Orin', python='3.10.12')

    def test_platform_pairs_and_wrong_runtime(self):
        from onboardmimic.backends.sonic.platforms import source_environment_errors
        good = self.facts()
        self.assertEqual(source_environment_errors(good), [])
        self.assertEqual(source_environment_errors(dict(good, ubuntu='20.04', l4t='35.3.1', tensorrt='8.5.2', cuda_runtime=11040)), [])
        for change in (dict(arch='x86_64'), dict(tensorrt='8.6.2'), dict(cuda_runtime=11040), dict(l4t='35.3.1'), dict(memory_kib=8*1024*1024)):
            with self.subTest(change=change):
                self.assertTrue(source_environment_errors(dict(good, **change)))

    def test_ros_selection_and_explicit_override(self):
        from onboardmimic.robots.unitree_g1 import setup_support as support
        self.assertEqual(support.ros_distribution(dict(ID='ubuntu', VERSION_ID='22.04')), 'humble')
        self.assertEqual(support.ros_distribution(dict(ID='ubuntu', VERSION_ID='20.04')), 'foxy')
        with self.assertRaises(ValueError):support.ros_distribution(dict(ID='ubuntu', VERSION_ID='24.04'))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ws = root/'setup.bash';ws.write_text('')
            with patch.object(support, 'probe_ros', return_value=(True,'')), patch.object(support, 'ros_distribution', side_effect=AssertionError('must honor override')):
                self.assertEqual(support.find_ros(root, ros=Path('/custom/setup.bash'), workspace=ws, home=root)[0], Path('/custom/setup.bash'))

    def test_host_pass_cannot_qualify_target_and_changed_files_rejected(self):
        from onboardmimic.backends.sonic import source_validation as v
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'var').mkdir()
            report={'software_passed':True,'host_test':True,'environment':self.facts(),'sha256':{'source':'abc'}}
            def save(): (root/v.REPORT).write_text(json.dumps(report))
            save()
            with self.assertRaisesRegex(RuntimeError,'host tests'):v.verify_report(root,self.facts())
            report['host_test']=False
            report['numerical']={'benchmark':{'errors':[], 'planner_present':True,'results':{name:{'completion_from_deadline':{'n':100,'over_budget':0,'max_ms':1}} for name in ('encoder_decoder','planner')}}}
            save()
            with self.assertRaisesRegex(RuntimeError,'Environment changed'):v.verify_report(root,dict(self.facts(),tensorrt='10.4.0'))
            with patch.object(v,'inventory',return_value={'source':'changed'}), patch.object(v,'check_loaders') as loader:
                with self.assertRaisesRegex(RuntimeError,'files changed'):v.verify_report(root,self.facts())
                loader.assert_not_called()
            with patch.object(v,'inventory',return_value={'source':'abc'}), patch.object(v,'check_loaders') as loader:
                self.assertTrue(v.verify_report(root,self.facts())['software_passed'])
                loader.assert_called_once()

    def test_inspection_never_runs_inference_or_native_programs(self):
        from onboardmimic.backends.sonic import source_validation as v
        import json
        with tempfile.TemporaryDirectory() as tmp, patch.object(v,'environment',return_value=self.facts()), patch.object(v.subprocess,'run') as run, contextlib.redirect_stdout(io.StringIO()):
            report=Path(tmp)/'report.json'
            self.assertEqual(v.main(['--report',str(report)]),0)
            run.assert_not_called()
            self.assertFalse(json.loads(report.read_text())['software_passed'])

    def test_source_wizard_requires_validation_before_services(self):
        from onboardmimic.backends.sonic import setup as wizard, source_validation as v
        with patch.object(wizard.os,'geteuid',return_value=1000), patch.object(v,'verify_report',side_effect=RuntimeError('not validated')), patch.object(wizard,'install') as install, patch.object(wizard,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'not validated'):wizard.main(['--source','--configure-only','--yes'])
            install.assert_not_called();run.assert_not_called()

    def test_running_controller_blocks_gpu_checks_and_clears_success(self):
        from onboardmimic.backends.sonic import source_validation as v
        import json
        with tempfile.TemporaryDirectory() as tmp, patch.object(v,'environment',return_value=self.facts()), patch.object(v,'require_idle',side_effect=RuntimeError('Controller running')), patch.object(v,'inventory') as inventory, contextlib.redirect_stdout(io.StringIO()):
            report=Path(tmp)/'report.json';report.write_text('{"software_passed":true}')
            self.assertEqual(v.main(['--inference','--report',str(report)]),1)
            inventory.assert_not_called()
            result=json.loads(report.read_text())
            self.assertFalse(result['software_passed'])
            self.assertIn('Controller running',result['error'])

class ROSInstallerTests(unittest.TestCase):
    def test_humble_and_foxy_dependency_selection(self):
        from onboardmimic.robots.unitree_g1 import install_dependencies as installer
        for version,distro in [('20.04','foxy'),('22.04','humble')]:
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                with patch('sys.argv',['installer','--workspace',str(root)]), patch.object(installer.os,'geteuid',return_value=1000), patch.object(installer.platform,'machine',return_value='aarch64'), patch.object(installer,'os_release',return_value={'ID':'ubuntu','VERSION_ID':version}), patch.object(installer,'require_idle'), patch.object(installer,'run') as run, patch.object(installer,'checkout') as checkout, patch.object(installer,'probe_ros',return_value=(True,'')), patch.object(Path,'exists',return_value=True), contextlib.redirect_stdout(io.StringIO()):
                    (root/'.onboardmimic-dependencies').write_text('ROS_DISTRO='+distro)
                    installer.main()
                self.assertEqual(checkout.call_count,3 if distro=='foxy' else 1)
                commands=[call.args[0] for call in run.call_args_list]
                packages=[c for c in commands if c[:3]==['sudo','apt-get','install']][0]
                self.assertIn('ros-'+distro+'-rmw-cyclonedds-cpp',packages)
                build=[c[-1] for c in commands if c[0]=='bash'][0]
                self.assertIn('/opt/ros/'+distro+'/setup.bash',build)
                if distro=='humble':
                    self.assertNotIn('src/rmw_cyclonedds',build)
                    self.assertNotIn('source install/setup.bash',build)
                self.assertFalse(any(c[0] in ('systemctl','ros2') for c in commands))

    def test_cross_distribution_workspace_is_preserved(self):
        from onboardmimic.robots.unitree_g1 import install_dependencies as installer
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);marker=root/'.onboardmimic-dependencies';marker.write_text('ROS_DISTRO=foxy')
            with patch('sys.argv',['installer','--workspace',str(root)]), patch.object(installer.os,'geteuid',return_value=1000), patch.object(installer.platform,'machine',return_value='aarch64'), patch.object(installer,'os_release',return_value={'ID':'ubuntu','VERSION_ID':'22.04'}), patch.object(installer,'require_idle'), patch.object(installer,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'distribution changed'):installer.main()
                run.assert_not_called()
            self.assertEqual(marker.read_text(),'ROS_DISTRO=foxy')

class TimingGateTests(unittest.TestCase):
    def test_deadline_miss_is_not_software_acceptance(self):
        from onboardmimic.backends.sonic.source_validation import check_timing
        report={'errors':[], 'planner_present':True, 'results':{name:{'completion_from_deadline':{'n':100,'over_budget':0,'max_ms':1}} for name in ('encoder_decoder','planner')}}
        check_timing(report)
        for bad in (dict(n=100,over_budget=1,max_ms=21),dict(n=0,over_budget=0,max_ms=0),dict(n=100,over_budget=0,max_ms=float('nan'))):
            report['results']['encoder_decoder']['completion_from_deadline']=bad
            with self.assertRaises(RuntimeError):check_timing(report)

class CommunityInstallerTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('jp6_installer',ROOT/'scripts/install_jp6.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_supported_patch_versions_and_carrier_name(self):
        from onboardmimic.backends.sonic.platforms import source_environment_errors
        facts=SourceValidationTests().facts()
        for l4t,trt in [('36.3.0','10.0.1'),('36.4.3','10.3.0'),('36.4.4','10.3.0.30')]:
            self.assertEqual(source_environment_errors(dict(facts,l4t=l4t,tensorrt=trt,board='Custom G1 carrier')),[])
        self.assertTrue(source_environment_errors(dict(facts,board='Other board',gpu='Other GPU')))

    def test_install_order_and_pose_dependencies(self):
        module=self.module()
        with patch.object(module.shutil,'which',return_value=None):
            steps=module.install_steps(Path('/tmp/new-ws'),True,False)
        commands=[[str(v) for v in command] for command,_ in steps]
        text=[' '.join(c) for c in commands]
        self.assertTrue(any('pico/requirements.txt' in c for c in text))
        self.assertIn(['sudo','apt-get','install','-y','docker.io'],commands)
        self.assertIn('--inference',commands[-2])
        self.assertIn('--source',commands[-1]);self.assertIn('--pico',commands[-1])
        self.assertEqual(steps[-1][1]['ROS_SETUP'],'/opt/ros/humble/setup.bash')
        self.assertEqual(steps[-1][1]['UNITREE_ROS_SETUP'],'/tmp/new-ws/install/setup.bash')
        self.assertFalse(any('start_sonic' in c or 'prepare_posture' in c for c in text))
        with patch.object(module.shutil,'which',return_value='/usr/bin/docker'):
            planner=module.install_steps(Path('/tmp/ws'),False,True)
        self.assertIn('--no-pico',planner[-1][0]);self.assertIn('--upgrade',planner[-1][0])
        self.assertFalse(any('pico/requirements.txt' in str(c) for c,_ in planner))

    def test_check_is_read_only_and_failed_step_stops_setup(self):
        module=self.module()
        from onboardmimic.backends.sonic import source_validation as v
        facts=SourceValidationTests().facts()
        with patch.object(module,'basic_environment',return_value=facts), patch.object(module,'ensure_tensorrt') as bootstrap, patch.object(v,'environment',return_value=facts), patch.object(module,'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(['--check']),0)
            bootstrap.assert_called_once_with(profile='jp6',development=True,check=True)
            run.assert_not_called()
        with patch.object(module,'basic_environment',return_value=facts), patch.object(module,'ensure_tensorrt') as bootstrap, patch.object(v,'environment',return_value=facts), patch.object(module.os,'geteuid',return_value=1000), patch.object(module,'require_idle'), patch.object(module,'run',side_effect=RuntimeError('build failed')) as run, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'build failed'):module.main(['--no-pico'])
            self.assertEqual(run.call_count,1)

    def test_wrong_platform_stops_before_runtime_or_install(self):
        module=self.module()
        from onboardmimic.backends.sonic import source_validation as v
        with patch.object(module,'basic_environment',return_value=dict(arch='x86_64',ubuntu='22.04',l4t='')), patch.object(v,'environment') as runtime, patch.object(module,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'Requires JetPack 6'):module.main([])
            runtime.assert_not_called();run.assert_not_called()
