"""Installer guards, discovery and migration without touching real services."""
import importlib.util
import json
import os
import pty
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import onboardmimic.robots.unitree_g1.setup_support as support
import scripts.install_services as services
import onboardmimic.backends.sonic.setup as wizard


class DiscoveryTest(unittest.TestCase):
    def test_environment_file_is_literal_and_roundtrips_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bridge.env'
            value = '/home/new user/$(touch never)/setup.bash'
            path.write_text('ROS_SETUP='+support.env_value(value)+'\nIGNORED=anything\n')
            self.assertEqual(support.read_env(path), {'ROS_SETUP': value})
            self.assertFalse((Path(tmp)/'never').exists())

    def test_explicit_workspace_wins_and_missing_path_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            home = Path(tmp)
            path = home/'cyclonedds_ws/install/setup.bash'
            path.parent.mkdir(parents=True)
            path.touch()
            with patch.object(support, 'probe_ros', return_value=(True, 'ok')):
                self.assertEqual(support.find_ros(home, workspace=path, home=home)[1], path)
                with self.assertRaisesRegex(RuntimeError, 'install_ros_dependencies'):
                    support.find_ros(home, workspace=home/'missing', home=home)

    def test_ambiguous_discovery_requires_explicit_selection(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            home = Path(tmp)
            for name in ('first', 'second'):
                path = home/name/'install/setup.bash'
                path.parent.mkdir(parents=True)
                path.touch()
            with patch.object(support, 'probe_ros', return_value=(True, 'ok')):
                with self.assertRaisesRegex(RuntimeError, 'Multiple ROS'):
                    support.find_ros(home, home=home)

    def test_ros_probe_quotes_paths_without_shell_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            modules = root/'modules'
            modules.mkdir()
            (modules/'rclpy.py').write_text('def init(*a): raise AssertionError("No ROS nodes during setup")\n')
            for name in ('unitree_hg', 'unitree_api', 'ament_index_python'):
                (modules/name).mkdir()
                (modules/name/'__init__.py').touch()
            (modules/'unitree_hg/msg.py').write_text('LowCmd = LowState = object\n')
            (modules/'unitree_api/msg.py').write_text('Request = Response = object\n')
            (modules/'rosidl_generator_py.py').write_text('def import_type_support(p): return None\n')
            (modules/'ament_index_python/packages.py').write_text('def get_package_prefix(p): return "/test"\n')
            # A literal shell substitution in a filename must remain a filename.
            ros = root/'ROS $(touch INJECTION).bash'
            ros.write_text('export PYTHONPATH='+shlex.quote(str(modules))+'\n')
            ws = root/'Unitree with spaces.bash'
            ws.write_text(':\n')
            ok, detail = support.probe_ros(ros, ws)
            self.assertTrue(ok, detail)
            self.assertFalse((Path.cwd()/'INJECTION').exists())


class GuardTest(unittest.TestCase):
    def test_command_bridge_and_python_controller_block_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            for pid, executable, argv in (
                ('12', '/test/robot_command_bridge', b'/test/robot_command_bridge\0'),
                ('13', '/usr/bin/python3', b'python3\0/opt/loopback_bridge.py\0'),
                ('14', '/bin/bash', b'bash\0-c\0pgrep -f robot_command_bridge\0'),
            ):
                entry=proc/pid;entry.mkdir()
                (entry/'exe').symlink_to(executable)
                (entry/'comm').write_text(Path(executable).name[:15])
                (entry/'cmdline').write_bytes(argv)
            self.assertEqual({pid for pid,_ in support.active_controllers(proc)}, {'12','13'})
            with patch.object(Path, 'read_bytes', side_effect=PermissionError('private command line')):
                self.assertEqual({pid for pid,_ in support.active_controllers(proc)}, {'12'})

    def test_install_stage_keeps_controlling_terminal_for_sudo(self):
        pid, terminal = pty.fork()
        if pid == 0:
            try:
                support.run([sys.executable, '-c', 'import os; os.close(os.open("/dev/tty", os.O_RDWR))'])
                os._exit(0)
            except BaseException:
                os._exit(1)
        try:
            while True:
                try:
                    if not os.read(terminal, 4096):
                        break
                except OSError:
                    break
            _, status = os.waitpid(pid, 0)
            self.assertEqual(status, 0)
        finally:
            os.close(terminal)

    def test_running_controller_blocks_child_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)/'proc'
            entry = proc/'1234'
            entry.mkdir(parents=True)
            (entry/'exe').symlink_to('/test/g1_deploy_onnx_ref')
            (entry/'comm').write_text('g1_deploy_onnx_r\n')
            owners = support.active_controllers(proc)
            self.assertEqual(owners, [('1234', 'g1_deploy_onnx_ref')])
            with patch.object(support, 'active_controllers', return_value=owners), \
                 patch.object(support.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(RuntimeError, 'Controller/bridge'):
                    support.run(['/bin/touch', str(Path(tmp)/'must-not-exist')])
                self.assertFalse((Path(tmp)/'must-not-exist').exists())
                launch.assert_not_called()

    def test_readonly_check_never_installs_or_starts(self):
        with patch.object(wizard, 'find_ros', return_value=(Path('/ROS'), Path('/WS'))), \
             patch.object(wizard.network, 'detect', return_value='eth1'), \
             patch.object(wizard, 'lan_addresses', return_value=[]), \
             patch.object(wizard, 'run') as run, patch.object(wizard, 'install') as install:
            wizard.main(['--check'])
            run.assert_not_called()
            install.assert_not_called()

    def test_install_only_does_not_modify_or_start_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'prebuilt_manifest.json').write_text('{}')
            with patch.object(wizard, 'ROOT', root), patch.object(Path, 'home', return_value=root), \
                 patch.object(wizard.os, 'geteuid', return_value=1000), \
                 patch.object(wizard, 'find_ros', return_value=(Path('/ROS'), Path('/WS'))), \
                 patch.object(wizard.network, 'detect', return_value='eth1'), \
                 patch.object(wizard, 'run') as run, patch.object(wizard, 'install') as install:
                wizard.main(['--yes', '--install-only', '--pico'])
                install.assert_not_called()
                self.assertEqual(run.call_count, 1)
                self.assertIn('--with-pico', run.call_args.args[0])
                self.assertFalse((root/'.config').exists())


class RecoveryTest(unittest.TestCase):
    def test_only_idle_endpoints_restart_after_clean_exit(self):
        units = services.render_units(Path('/app'), Path('/config'))
        for name in services.BOOT_UNITS:
            self.assertIn('Restart=always\nRestartSec=3', units[name])
            self.assertNotIn('ExecStartPost=', units[name])
        for name in ('onboardmimic-bridge.service', 'onboardmimic-console-connect.service'):
            self.assertIn('Restart=no', units[name])
            self.assertNotIn('[Install]', units[name])

    def test_generated_access_relocation_preserves_mode(self):
        for mode in ('lan', 'local'):
            old = wizard.access_dropin(Path('/old path'), Path('/config'), mode)
            new = services.migrate_generated_dropin('onboardmimic-console.service', old, Path('/old path'), Path('/new path'), Path('/config'))
            self.assertEqual(new, wizard.access_dropin(Path('/new path'), Path('/config'), mode))
            self.assertIsNone(services.migrate_generated_dropin('onboardmimic-console.service', old+'Environment=CUSTOM=1\n', Path('/old path'), Path('/new path'), Path('/config')))

    def test_readiness_requires_idle_backend(self):
        import io
        with patch.object(wizard.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(json.dumps({'state': 'stopped'}).encode())):
            wizard.wait_console(timeout=1)
        with patch.object(wizard.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(json.dumps({'state': 'active'}).encode())):
            with self.assertRaisesRegex(RuntimeError, 'readiness failed'):
                wizard.wait_console(timeout=0.01)


if __name__ == '__main__':
    unittest.main()
