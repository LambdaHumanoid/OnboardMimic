"""Hardware-free contract tests with a two-joint body and unrelated S0 protocol."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from onboardmimic.core import registry
from onboardmimic.core.contracts import RobotAdapter, S0Backend, BackendProgress
from onboardmimic.core.lifecycle import Lifecycle
from onboardmimic.core.paths import ROOT


class TwoJointRobot(RobotAdapter):
    id = 'test_body'
    label = 'Two-joint fixture'
    joint_count = 2
    state_topics = frozenset(('fixture/state',))

    def telemetry(self, latest, target, now):
        stamp, q = latest.get('fixture/state', (-100., []))
        return dict(q=q, dq=[0., 0.], fresh=now-stamp<.2, valid=len(q)==2,
                    faults=[], upright=True, max_speed_rad_s=0., target_error_rad=0.)

    def safe(self, telemetry):
        return telemetry['fresh'] and telemetry['valid']

    def guard(self, owned_pid=None):
        pass

    def connection(self):
        raise AssertionError('No hardware connection during contract tests')

    def service_units(self, root, config):
        return {}


class OtherBackend(S0Backend):
    id = 'test_s0'
    label = 'Fixture S0'
    compatible_robots = frozenset(('test_body',))
    capabilities = frozenset(('planner',))
    max_speed = .8
    prepare_seconds = 7
    handoff_seconds = 4

    def baseline(self, robot):
        return [.1, -.1]

    def launch_spec(self, robot):
        raise AssertionError('No native executable in this test')

    def progress(self, text):
        return BackendProgress(prepared=text=='fixture-ready', active=text=='fixture-running')

    def decode_status(self, topic, value):
        return dict(mode='planner', playing=True) if topic=='fixture/policy' else None

    def transport(self, gateway, robot):
        raise AssertionError('No sockets in this test')


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.robots=patch.dict(registry.ROBOTS, {'test_body':TwoJointRobot})
        self.backends=patch.dict(registry.BACKENDS, {'test_s0':OtherBackend})
        self.robots.start();self.backends.start()
        self.addCleanup(self.robots.stop);self.addCleanup(self.backends.stop)

    def test_alternate_pair_completes_lifecycle_without_g1_telemetry_or_sonic_logs(self):
        robot, backend = registry.select('test_body','test_s0')
        process=Mock(pid=123);process.poll.return_value=None
        launch=Mock(return_value=process);clock=[100.];log=[''];seq=[0]
        g=Lifecycle(backend.baseline(robot),launch,lambda:log[0],robot.guard,
                    robot=robot,backend=backend,clock=lambda:clock[0])
        g.ingest('fixture/state',[.1,-.1])
        def request(route='/api/controller', **data):
            seq[0]+=1
            return g.request(route,dict(client='fixture',seq=seq[0],epoch=g.epoch,ticket=g.ticket(),**data))
        self.assertTrue(g.status()['prepare_available']);launch.assert_not_called()
        request(action='prepare_pose');self.assertEqual(g.phase,'loading')
        self.assertFalse(g.start_pending)
        log[0]='fixture-ready';g.tick();self.assertEqual(g.phase,'prepared')
        request(action='start_policy');self.assertEqual(g.phase,'starting_policy')
        log[0]='fixture-running';g.tick();self.assertEqual(g.phase,'active')
        self.assertEqual(g.status()['robot_id'],'test_body')
        self.assertEqual(g.status()['prepare_seconds'],7)
        self.assertEqual(g.status()['reference_planner_handoff_seconds'],4)
        self.assertFalse(g.status()['input_mode_switch_available'])
        request('/api/control',move='forward',speed=.8)
        self.assertEqual(g.last_packet['speed'],.8)
        with self.assertRaises(ValueError):request('/api/control',move='forward',speed=.81)
        with self.assertRaisesRegex(ValueError,'模式'):request(action='select_pose')
        # Missing telemetry is handled by the robot adapter's own freshness rule.
        clock[0]+=.201;g.tick();process.kill.assert_called_once()
        self.assertEqual(g.phase,'error')

    def test_unknown_incompatible_and_invalid_reference_refused_before_launch(self):
        for pair in [('missing','test_s0'),('test_body','missing'),('unitree_g1','test_s0'),('test_body','sonic')]:
            with self.subTest(pair=pair),self.assertRaises(ValueError):registry.select(*pair)
        for baseline in ([0.], [0., float('nan')], [False,0.]):
            with patch.object(OtherBackend,'baseline',return_value=baseline),self.assertRaises(ValueError):
                registry.select('test_body','test_s0')

    def test_service_units_select_both_adapters_explicitly(self):
        from scripts.install_services import render_units
        units=render_units(Path('/fixture path'),Path('/config'),robot='test_body',backend='test_s0')
        self.assertEqual(len(units),2)
        for unit in units.values():
            self.assertIn('--robot "test_body" --backend "test_s0"',unit)
            self.assertNotIn('unitree_g1',unit)
            self.assertNotIn('prepare_pose',unit)
        with self.assertRaises(ValueError):render_units(Path('/x'),Path('/y'),robot='test_body',backend='sonic')

    def test_catalog_uses_adapter_contracts_without_hardware_or_launch(self):
        with patch.object(TwoJointRobot,'connection') as connection, \
             patch.object(TwoJointRobot,'guard') as guard, \
             patch.object(OtherBackend,'launch_spec') as launch, \
             patch.object(OtherBackend,'transport') as transport:
            catalog = registry.catalog()
        for method in (connection,guard,launch,transport):method.assert_not_called()
        self.assertEqual(catalog['pairs'], [
            {'robot_id':'test_body','backend_id':'test_s0'},
            {'robot_id':'unitree_g1','backend_id':'sonic'}])
        backend=next(b for b in catalog['backends'] if b['id']=='test_s0')
        self.assertEqual(backend['capabilities'],['planner'])
        self.assertEqual(backend['max_speed'],.8)
        with patch.object(OtherBackend,'baseline',return_value=[0.]):
            with self.assertRaises(ValueError):registry.catalog()

    def test_console_catalog_matches_explicit_pair(self):
        from onboardmimic.console.server import Console
        connection=Mock()
        console=Console(Mock(),ROOT/'onboardmimic/console/static',connection=connection,
                        robot_id='test_body',backend_id='test_s0')
        self.assertEqual(console.platform()['selected'],{'robot_id':'test_body','backend_id':'test_s0'})
        connection.assert_not_called()

    def test_setup_dispatch_refuses_unsupported_pair_without_installing(self):
        from scripts.setup import main
        with patch.object(OtherBackend,'installer') as installer:
            with self.assertRaises(ValueError):main(['--robot','unitree_g1','--backend','test_s0','--yes'])
            installer.assert_not_called()
        with patch.object(OtherBackend,'installer') as installer:
            main(['--robot','test_body','--backend','test_s0','--check'])
            installer.return_value.assert_called_once_with(['--check'])

    def test_selection_does_not_load_tensorrt_ros_or_tracking_dependencies(self):
        code='''import sys
from onboardmimic.core.registry import select, select_input, catalog
robot,backend=select()
select_input('pico')
catalog()
assert not any(x in sys.modules for x in ('rclpy','tensorrt','scipy','zmq','torch'))
assert robot.joint_count == len(backend.baseline(robot)) == 29
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_relocated_native_source_matches_frozen_content(self):
        # Imported native source hashes stay relative to the backend deployment tree.
        import hashlib
        hashes=json.loads((ROOT/'onboardmimic/backends/sonic/native_source_manifest.json').read_text())
        for name, digest in hashes.items():
            path=ROOT/'onboardmimic/backends/sonic/deploy'/name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest,name)

    def test_generic_modules_do_not_import_hardware_or_backend_implementations(self):
        import ast
        for folder in ('core','console'):
            for path in (ROOT/'onboardmimic'/folder).glob('*.py'):
                for node in ast.walk(ast.parse(path.read_text())):
                    modules=([node.module or ''] if isinstance(node,ast.ImportFrom) else
                             [x.name for x in node.names] if isinstance(node,ast.Import) else [])
                    self.assertFalse(any(x.startswith(('onboardmimic.robots.','onboardmimic.backends.','onboardmimic.inputs.')) for x in modules),str(path))


class ConsoleSelectionTest(unittest.TestCase):
    def test_mismatched_runtime_blocks_start_but_still_attempts_estop(self):
        from onboardmimic.console.client import OnboardBackend
        client=Mock()
        client.status.return_value={'mode':'onboard_real','robot_id':'different','backend':'sonic'}
        console=OnboardBackend(client,robot_id='unitree_g1',backend_id='sonic')
        self.assertEqual(console.status()['state'],'error')
        with self.assertRaises(RuntimeError):console.command('prepare_pose')
        client.command.assert_not_called()
        console.command('estop');client.command.assert_called_once_with('estop')
