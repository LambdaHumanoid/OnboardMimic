import tempfile
from pathlib import Path
import unittest

from onboardmimic.robots.unitree_g1.preflight import assess, control_process, processes


class PreflightTest(unittest.TestCase):
    def test_inactive_unit_does_not_hide_running_bridge(self):
        blockers = assess({'binary': {'present': True}},
                          {'bridge': {'LoadState': 'loaded', 'ActiveState': 'inactive'}},
                          [{'pid': 123, 'argv': ['g1_bridge_node']}], [])
        self.assertIn('existing_control_processes', blockers)

    def test_empty_machine_is_not_implicitly_motion_ready(self):
        self.assertEqual(assess({}, {}, [], []),
                         ['physical_commissioning_pending'])

    def test_unknown_service_and_permission_errors_fail_closed(self):
        self.assertIn('inspection_incomplete', assess({}, {'bridge': {}}, [], []))
        self.assertIn('inspection_incomplete', assess({}, {}, [], ['denied']))

    def test_active_service_blocks_even_without_visible_process(self):
        self.assertIn('existing_control_services', assess({}, {
            'teleop': {'LoadState': 'loaded', 'ActiveState': 'activating'}}, [], []))

    def test_exact_process_arguments_not_shell_search_text(self):
        self.assertTrue(control_process(['python3', '/opt/g1_bridge_node', '--live']))
        self.assertTrue(control_process(['/opt/g1_deploy_onnx_ref', 'lo']))
        self.assertFalse(control_process(['bash', '-c', 'pgrep -f g1_bridge_node']))
        self.assertFalse(control_process(['python3', 'test_g1_bridge_node.py']))

    def test_command_bridge_detection_is_independent_of_application_name(self):
        self.assertTrue(control_process(['/opt/another app/robot_command_bridge']))
        self.assertTrue(control_process(['python3', '/opt/loopback_bridge.py']))
        self.assertFalse(control_process(['bash', '-c', 'pgrep -f robot_command_bridge']))
        self.assertFalse(control_process(['python3', 'test_robot_command_bridge.py']))

    def test_read_only_arguments_never_become_programs(self):
        for controller in ('g1_deploy_onnx_ref', 'robot_command_bridge', 'loopback_bridge.py'):
            path = '/opt/robot/' + controller
            for argv in ([tool, path] for tool in ('sha256sum', 'cat', 'stat', 'ls', 'file', 'strings', 'less')):
                with self.subTest(argv=argv):self.assertFalse(control_process(argv))
            for argv in (['python3', '/tmp/inspect.py', path],
                         ['python3', '-c', 'print(1)', path],
                         ['python3', '-m', 'unittest', path],
                         ['python3', '--version', path],
                         ['bash', '-n', path],
                         ['bash', '/tmp/inspect.sh', path],
                         ['bash', '-lc', 'cat "$1"', 'shell', path],
                         ['stdbuf', '-oL', 'cat', path]):
                with self.subTest(argv=argv):self.assertFalse(control_process(argv))

    def test_real_entries_with_interpreter_flags_and_launch_wrapper(self):
        cases = [
            ['python3.8', '-u', '-W', 'ignore', '/opt/loopback_bridge.py', '--no-release-mode'],
            ['python3', '-X', 'dev', '--', '/opt/start_g1_teleop.py'],
            ['python3', '-m', 'g1_bridge_node'],
            ['bash', '-e', '/opt/run_scene_console.sh'],
            ['bash', '--norc', '--noprofile', '/opt/run_scene_console.sh'],
            ['bash', '-o', 'pipefail', '/opt/run_scene_console.sh'],
            ['stdbuf', '-oL', '-eL', '/opt/g1_deploy_onnx_ref'],
            ['python3', '/opt/owned_exec.py', '1234', '/usr/bin/stdbuf', '-oL', '-eL', '/opt/g1_deploy_onnx_ref', 'eth0'],
        ]
        for argv in cases:
            with self.subTest(argv=argv):self.assertTrue(control_process(argv))
        for argv in ([], ['python3'], ['python3', '-W'], ['python3', '-m'],
                     ['bash', '-c'], ['python3', '/opt/owned_exec.py', '1234']):
            with self.subTest(argv=argv):self.assertFalse(control_process(argv))

    def test_proc_scan_distinguishes_file_reader_from_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = {10: ['sha256sum', '/opt/g1_deploy_onnx_ref'],
                    11: ['cat', '/opt/loopback_bridge.py'],
                    12: ['python3', '-u', '/opt/loopback_bridge.py'],
                    13: ['/opt/g1_deploy_onnx_ref', 'eth0']}
            for pid, argv in rows.items():
                d = root/str(pid);d.mkdir()
                (d/'cmdline').write_bytes(b'\0'.join(x.encode() for x in argv))
            found, errors = processes(root)
            self.assertEqual([x['pid'] for x in found], [12, 13])
            self.assertEqual(errors, [])

    def test_proc_scan_ignores_exited_and_nonprocess_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '42').mkdir()
            (root / '42' / 'cmdline').write_bytes(b'python3\0/opt/g1_bridge_node\0')
            (root / '43').mkdir()
            (root / 'sys').mkdir()
            found, errors = processes(root)
            self.assertEqual([item['pid'] for item in found], [42])
            self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
