"""Release portability and no-hardware regression tests."""
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj

class ReleaseTest(unittest.TestCase):
    def test_default_pose_matches_controller(self):
        s=(ROOT/'onboardmimic/backends/sonic/deploy/src/g1/g1_deploy_onnx_ref/include/policy_parameters.hpp').read_text()
        block=s.split('const std::array<double, 29> default_angles = {')[1].split('};')[0]
        block=re.sub(r'//[^\n]*','',block)
        values=[float(x.strip()) for x in block.split(',') if x.strip()]
        self.assertEqual(values,json.loads((ROOT/'onboardmimic/backends/sonic/deploy/onboard/baseline_q.json').read_text()))

    def test_runtime_uses_relocated_native_binary(self):
        from onboardmimic.backends.sonic.adapter import controller_args
        folder=ROOT/'onboardmimic/backends/sonic/deploy/onboard'
        self.assertEqual(Path(controller_args(folder,'eth1')[0]).resolve(),ROOT/'onboardmimic/backends/sonic/deploy/target/release/g1_deploy_onnx_ref')

    def test_model_import_rejects_corruption_without_promoting(self):
        from onboardmimic.backends.sonic import download_models as d
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'onboardmimic/backends/sonic').mkdir(parents=True);(root/'onboardmimic/backends/sonic/resources.json').write_text(json.dumps({'models':{'files':{'m.onnx':{'destination':'policy/m.onnx','sha256':'wrong'}}}}))
            source=root/'input/policy';source.mkdir(parents=True);(source/'m.onnx').write_bytes(b'bad')
            with patch.object(d,'ROOT',root),patch.object(sys,'argv',['download','--models-dir',str(root/'input')]):
                with self.assertRaises(SystemExit):d.main()
            self.assertFalse((root/'onboardmimic/backends/sonic/deploy/policy/m.onnx').exists())

    def test_service_installer_enables_only_idle_api_and_console(self):
        d=module('install_test','scripts/install_services.py')
        with tempfile.TemporaryDirectory() as tmp,patch.object(d.Path,'home',return_value=Path(tmp)),patch.object(d.subprocess,'run') as run:
            run.return_value.returncode=0
            d.main([])
            commands=[c.args[0] for c in run.call_args_list]
            self.assertEqual(commands[0][:2],['loginctl','enable-linger'])
            self.assertEqual(commands[1],['systemctl','--user','daemon-reload'])
            self.assertEqual(commands[2],['systemctl','--user','enable','onboardmimic.service','onboardmimic-console.service'])
            self.assertFalse(any('start' in c or '--now' in c for c in commands))
            token=Path(tmp)/'.config/onboardmimic/http.token'
            self.assertEqual(token.stat().st_mode & 0o777,0o600)
            value=token.read_text();d.main([]);self.assertEqual(token.read_text(),value)
            unit=(Path(tmp)/'.config/systemd/user/onboardmimic.service').read_text()
            self.assertIn('--physical',unit);self.assertIn('WantedBy=default.target',unit)
            for name in ('onboardmimic-bridge.service','onboardmimic-console-connect.service',
                         'onboardmimic-state-primary.service','onboardmimic-state-standby.service','onboardmimic-state-relay.service'):
                self.assertNotIn('[Install]',(Path(tmp)/'.config/systemd/user'/name).read_text())

    def test_linger_failure_does_not_claim_boot_setup_complete(self):
        d=module('install_linger_test','scripts/install_services.py')
        with tempfile.TemporaryDirectory() as tmp,patch.object(d.Path,'home',return_value=Path(tmp)),patch.object(d.subprocess,'run') as run:
            run.return_value.returncode=1;run.return_value.stderr='Permission denied'
            with self.assertRaisesRegex(SystemExit,'sudo loginctl enable-linger'):d.main([])
            self.assertFalse(any('enable' in c.args[0] for c in run.call_args_list))

    def test_cli_rejected_control_does_not_stop_another_owner(self):
        d=module('cli_test','scripts/control.py')
        with tempfile.TemporaryDirectory() as tmp:
            token=Path(tmp)/'http.token';token.write_text('x'*40);token.chmod(0o600)
            with patch.object(sys,'argv',['control','forward','--token-file',str(token)]),patch.object(d,'OnboardClient') as client:
                client.return_value.control.side_effect=RuntimeError('owned')
                with self.assertRaises(RuntimeError):d.main()
                self.assertEqual(client.return_value.control.call_count,1)

    def test_cli_status_does_not_send_motion(self):
        d=module('cli_status','scripts/control.py')
        with tempfile.TemporaryDirectory() as tmp:
            token=Path(tmp)/'http.token';token.write_text('x'*40);token.chmod(0o600)
            with patch.object(sys,'argv',['control','--token-file',str(token)]),patch.object(d,'OnboardClient') as client:
                client.return_value.status.return_value={'state':'stopped'}
                d.main();client.return_value.control.assert_not_called();client.return_value.command.assert_not_called()

    def test_cli_target_speed_boundary(self):
        d=module('cli_speed','scripts/control.py')
        with tempfile.TemporaryDirectory() as tmp:
            token=Path(tmp)/'http.token';token.write_text('x'*40);token.chmod(0o600)
            with patch.object(sys,'argv',['control','forward','--speed','5','--duration','0.001','--token-file',str(token)]),patch.object(d,'OnboardClient') as client,patch.object(d.time,'monotonic',side_effect=[0.,1.]):
                d.main()
                self.assertEqual(client.return_value.control.call_args_list[0].args[0]['speed'],5.)
                self.assertTrue(client.return_value.control.call_args_list[-1].args[0]['stop'])
            with patch.object(sys,'argv',['control','forward','--speed','5.0001']),patch.object(d,'OnboardClient') as client,patch.object(sys,'stderr'):
                with self.assertRaises(SystemExit):d.main()
                client.assert_not_called()

if __name__=='__main__':unittest.main()
