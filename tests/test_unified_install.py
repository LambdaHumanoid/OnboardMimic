"""Unified installation with isolated files and no physical/system operations."""
from contextlib import ExitStack
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch, Mock
from onboardmimic.backends.sonic import setup as wizard
from scripts import prepare_lab_install as lab
from scripts import install_services as services


class UnifiedInstallTest(unittest.TestCase):
    def environment(self, directory):
        home=Path(directory);root=home/'OM';root.mkdir()
        (root/'prebuilt_manifest.json').write_text('{}')
        (root/'.venv/bin').mkdir(parents=True);(root/'.venv/bin/python').touch()
        stack=ExitStack();self.addCleanup(stack.close)
        def fake_install(**kwargs):
            config=home/'.config/onboardmimic';config.mkdir(parents=True,exist_ok=True)
            token=config/'http.token';token.write_text('a'*48);token.chmod(0o600)
        mocks={}
        stack.enter_context(patch.object(wizard,'ROOT',root))
        stack.enter_context(patch.object(Path,'home',return_value=home))
        stack.enter_context(patch.object(wizard.os,'geteuid',return_value=1050))
        for target,name in [(wizard,'require_idle'),(wizard,'require_stopped'),(services,'require_stopped'),
                            (wizard,'configure'),(wizard,'wait_console'),(wizard,'wait_lab'),(wizard,'show_urls'),
                            (wizard,'run'),(lab,'install_dependencies')]:
            mocks[name]=stack.enter_context(patch.object(target,name))
        stack.enter_context(patch('onboardmimic.robots.unitree_g1.setup_support.require_idle'))
        stack.enter_context(patch.object(wizard,'find_ros',return_value=(Path('/ros'),Path('/ws'))))
        stack.enter_context(patch.object(wizard.network,'detect',return_value='eth0'))
        stack.enter_context(patch.object(wizard,'lan_addresses',return_value=[('wlan0','10.0.0.3')]))
        mocks['install']=stack.enter_context(patch.object(wizard,'install',side_effect=fake_install))
        mocks['system']=stack.enter_context(patch.object(lab.subprocess,'run',return_value=Mock(returncode=0,stdout='inactive')))
        return home,root,mocks

    def test_default_installs_lab_with_protection_and_starts_only_idle_units(self):
        with tempfile.TemporaryDirectory() as d:
            home,root,m=self.environment(d)
            wizard.main(['--yes','--configure-only'])
            units=home/'.config/systemd/user';config=home/'.config/onboardmimic'
            self.assertIn('--host 127.0.0.1',(units/'onboardmimic-lab-console.service').read_text())
            self.assertIn('http://127.0.0.1:8773/embed',(units/'onboardmimic-console.service.d/90-lab.conf').read_text())
            runtime=(units/'onboardmimic.service.d/90-lab.conf').read_text()
            self.assertIn('--enable-reference-replay',runtime)
            capture=json.loads((config/'collection.json').read_text())
            self.assertIn(capture['controller_spec_file'],runtime)
            self.assertEqual(capture['camera_port'],18554)
            self.assertIn(str(config/'protection/control.sock'),runtime)
            self.assertIn(str(config/'protection/control.sock'),(units/'onboardmimic-bridge.service.d/90-lab.conf').read_text())
            m['install_dependencies'].assert_called_once()
            commands=[c.args[0] for c in m['run'].call_args_list]
            self.assertEqual(commands,[['systemctl','--user','start',*wizard.BOOT_UNITS,*lab.LAB_UNITS]])
            m['wait_lab'].assert_called_once()
            m['show_urls'].assert_called_once_with('local','http://127.0.0.1:8769')

    def test_om_only_omits_lab(self):
        with tempfile.TemporaryDirectory() as d:
            home,root,m=self.environment(d)
            wizard.main(['--yes','--configure-only','--no-lab'])
            m['install_dependencies'].assert_not_called();m['wait_lab'].assert_not_called()
            self.assertFalse((home/'.config/onboardmimic/collection.json').exists())
            self.assertEqual(m['run'].call_args.args[0],['systemctl','--user','start',*wizard.BOOT_UNITS])

    def test_install_only_has_no_config_or_service_mutations(self):
        with tempfile.TemporaryDirectory() as d:
            home,root,m=self.environment(d)
            wizard.main(['--yes','--install-only'])
            m['install_dependencies'].assert_called_once();m['install'].assert_not_called()
            self.assertFalse((home/'.config').exists())
            self.assertFalse(any('systemctl' in c.args[0] for c in m['run'].call_args_list))

    def test_dependency_failure_starts_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            home,root,m=self.environment(d)
            m['install_dependencies'].side_effect=RuntimeError('dependency failed')
            with self.assertRaisesRegex(RuntimeError,'dependency failed'):wizard.main(['--yes','--configure-only'])
            m['install'].assert_not_called();m['run'].assert_not_called()

    def test_custom_configuration_is_rejected_before_any_installation(self):
        with tempfile.TemporaryDirectory() as d:
            home,root,m=self.environment(d)
            config=home/'.config/onboardmimic';config.mkdir(parents=True)
            (config/'collection.json').write_text('{"root":"/data/existing"}')
            with self.assertRaisesRegex(ValueError,'Existing configuration preserved'):wizard.main(['--yes'])
            m['install_dependencies'].assert_not_called();m['install'].assert_not_called();m['run'].assert_not_called()
            self.assertEqual((config/'collection.json').read_text(),'{"root":"/data/existing"}')

    def test_lan_origin_and_console_binding(self):
        with tempfile.TemporaryDirectory() as d:
            home,root,m=self.environment(d)
            wizard.main(['--yes','--configure-only','--access','lan','--origin','http://10.0.0.8:8769'])
            text=(home/'.config/systemd/user/onboardmimic-lab-console.service').read_text()
            self.assertIn('--host 0.0.0.0',text);self.assertIn('http://10.0.0.8:8769',text)
            self.assertIn('http://10.0.0.8:8773/embed',(home/'.config/systemd/user/onboardmimic-console.service.d/90-lab.conf').read_text())

    def test_existing_bridge_endpoint_conflict_stops_before_dependencies(self):
        with tempfile.TemporaryDirectory() as d:
            home,root,m=self.environment(d)
            config=home/'.config/onboardmimic';config.mkdir(parents=True)
            (config/'bridge.env').write_text('ONBOARDMIMIC_EXPERIMENTAL_PROTECTION_SOCKET=/custom/protection.sock\n')
            with self.assertRaisesRegex(ValueError,'Existing bridge configuration preserved'):
                wizard.main(['--yes','--configure-only'])
            m['install_dependencies'].assert_not_called();m['install'].assert_not_called()

    def test_pico_and_lab_share_generated_runtime_command(self):
        with tempfile.TemporaryDirectory() as d:
            home,root,m=self.environment(d)
            with patch.object(wizard,'prepare_pico'):
                wizard.main(['--yes','--configure-only','--pico'])
            text=(home/'.config/systemd/user/onboardmimic.service.d/90-lab.conf').read_text()
            self.assertIn('--pose-status',text);self.assertIn('--enable-reference-replay',text)
            self.assertIn('--experimental-protection-socket',text)

    def test_origin_validation(self):
        for bad in ('https://robot:8769','http://robot:8773','http://u:p@robot:8769','http://robot:8769/path'):
            with self.assertRaises(ValueError):wizard.lab_origin('lan',bad)
        with self.assertRaises(ValueError):wizard.lab_origin('local','http://robot:8769')
        with patch.object(wizard,'lan_addresses',return_value=[]):
            with self.assertRaisesRegex(ValueError,'--origin'):wizard.lab_origin('lan')
        self.assertEqual(wizard.lab_origin('local'),'http://127.0.0.1:8769')
