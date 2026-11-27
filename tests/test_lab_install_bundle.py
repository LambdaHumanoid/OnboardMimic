import json
from pathlib import Path
import unittest
from scripts.prepare_lab_install import bundle

class LabInstallBundleTest(unittest.TestCase):
    def test_complete_independent_protected_configuration(self):
        files=bundle(Path('/opt/OM'),Path('/home/robot'),'http://robot:8769',True)
        runtime=files['units/onboardmimic.service.d/90-lab.conf']
        self.assertIn('--enable-reference-replay',runtime)
        self.assertIn('--experimental-protection-socket',runtime)
        self.assertIn('--pose-status',runtime)
        self.assertIn('OM_SONIC_CONSUMED_TELEMETRY_PORT=16691',runtime)
        self.assertIn('/opt/OM/.lab-venv/bin/python',files['units/onboardmimic-lab-collector.service'])
        self.assertNotIn('PartOf=',files['units/onboardmimic-lab-collector.service'])
        capture=json.loads(files['config/collection.json'])
        self.assertEqual(capture['capture_adapter'],'sonic-native-v2')
        self.assertNotIn('max_duration_s',capture)
        self.assertIn(capture['controller_spec_file'],runtime)
        self.assertNotIn('systemctl',files['install-dependencies.sh'])
    def test_reject_invalid_origin_and_paths(self):
        for origin in ('file:///tmp/x','http://user:password@host','http://host/path','http://host?query'):
            with self.assertRaises(ValueError):bundle(Path('/opt/OM'),Path('/home/robot'),origin)
        with self.assertRaises(ValueError):bundle(Path('relative'),Path('/home/robot'),'http://host')
        with self.assertRaisesRegex(ValueError,'107 bytes'):
            bundle(Path('/opt/OM'),Path('/home')/('long-home-'*10),'http://host')

    def test_apply_preserves_conflicting_config_before_installing_dependencies(self):
        import tempfile
        from unittest.mock import patch
        from scripts.prepare_lab_install import install_bundle
        with tempfile.TemporaryDirectory() as d:
            home=Path(d); root=home/'OM'; config=home/'.config/onboardmimic'
            config.mkdir(parents=True)
            token=config/'http.token';token.write_text('a'*48);token.chmod(0o600)
            current=config/'collection.json';current.write_text('custom')
            files=bundle(root,home,'http://robot:8769')
            with patch('scripts.prepare_lab_install.os.geteuid',return_value=1050),patch('scripts.prepare_lab_install.Path.home',return_value=home),patch('scripts.install_services.require_stopped'),patch('onboardmimic.robots.unitree_g1.setup_support.require_idle'),patch('scripts.prepare_lab_install.subprocess.run') as run:
                with self.assertRaisesRegex(ValueError,'Existing configuration preserved'):
                    install_bundle(root,home,home/'bundle',files)
                run.assert_not_called()
            self.assertEqual(current.read_text(),'custom')

    def test_apply_starts_no_services_and_keeps_existing_token(self):
        import tempfile
        from unittest.mock import patch
        from scripts.prepare_lab_install import install_bundle
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);root=home/'OM'; config=home/'.config/onboardmimic';config.mkdir(parents=True)
            token=config/'http.token';token.write_text('a'*48);token.chmod(0o600)
            files=bundle(root,home,'http://robot:8769',True)
            with patch('scripts.prepare_lab_install.os.geteuid',return_value=1050),patch('scripts.prepare_lab_install.Path.home',return_value=home),patch('scripts.install_services.require_stopped'),patch('onboardmimic.robots.unitree_g1.setup_support.require_idle'),patch('scripts.prepare_lab_install.subprocess.run') as run:
                install_bundle(root,home,home/'bundle',files)
            calls=[c.args[0] for c in run.call_args_list]
            self.assertFalse(any('start' in c or '--now' in c for c in calls))
            self.assertEqual(token.read_text(),'a'*48)
            self.assertEqual((config/'protection').stat().st_mode & 0o777,0o700)
            self.assertEqual((config/'collection.json').stat().st_mode & 0o777,0o600)

    def test_busy_controller_prevents_dependency_and_config_changes(self):
        import tempfile
        from unittest.mock import patch
        from scripts.prepare_lab_install import install_bundle
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);root=home/'OM';files=bundle(root,home,'http://robot:8769')
            with patch('scripts.prepare_lab_install.os.geteuid',return_value=1050),patch('scripts.prepare_lab_install.Path.home',return_value=home),patch('onboardmimic.robots.unitree_g1.setup_support.require_idle',side_effect=RuntimeError('busy')),patch('scripts.prepare_lab_install.subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError,'busy'):install_bundle(root,home,home/'bundle',files)
                run.assert_not_called()
            self.assertFalse((home/'.config').exists())
