"""Migration uses temporary units and mock systemd, never live services."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('service_upgrade',ROOT/'scripts/install_services.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.config=self.home/'.config/onboardmimic';self.config.mkdir(parents=True)
        self.units=self.home/'.config/systemd/user';self.units.mkdir(parents=True)
        self.config.joinpath('http.token').write_text('existing-secret-token');self.config.joinpath('http.token').chmod(0o600)
        self.config.joinpath('bridge.env').write_text('ROS_SETUP=/custom/setup.bash\n')
        self.previous=Path('/opt/OnboardMimic previous release')
        self.old=m.render_units(self.previous,self.config)
        for name,data in self.old.items():(self.units/name).write_text(data)
        self.home_patch=patch.object(m.Path,'home',return_value=self.home);self.home_patch.start()
        self.runner=patch.object(m.subprocess,'run');self.run=self.runner.start()
        self.run.return_value=subprocess.CompletedProcess([],0,'LoadState=loaded\nActiveState=inactive\n','')
        self.quiet=patch('builtins.print');self.quiet.start()
    def tearDown(self):
        self.quiet.stop();self.runner.stop();self.home_patch.stop();self.tmp.cleanup()
    def current(self):return {p.name:p.read_text() for p in self.units.glob('*.service')}
    def test_explicit_upgrade_relocates_services_and_preserves_config(self):
        drop=self.units/'onboardmimic.service.d';drop.mkdir();(drop/'custom.conf').write_text('[Service]\nEnvironment=EXAMPLE=1\n')
        m.main(['--upgrade'])
        self.assertEqual(self.current(),m.render_units(m.ROOT,self.config))
        self.assertEqual((self.config/'http.token').read_text(),'existing-secret-token')
        self.assertEqual((self.config/'bridge.env').read_text(),'ROS_SETUP=/custom/setup.bash\n')
        self.assertEqual((drop/'custom.conf').read_text(),'[Service]\nEnvironment=EXAMPLE=1\n')
        backups=list((self.config/'backups').iterdir());self.assertEqual(len(backups),1)
        for name,data in self.old.items():self.assertEqual((backups[0]/name).read_text(),data)
        self.assertFalse(any('start' in c.args[0] or 'restart' in c.args[0] or 'stop' in c.args[0] or '--now' in c.args[0] for c in self.run.call_args_list))
        m.main([])  # Reinstallation is idempotent.
    def test_partial_installation_gains_console_without_starting(self):
        for name in ('onboardmimic-console.service','onboardmimic-console-connect.service'):
            (self.units/name).unlink()
        m.main(['--upgrade'])
        self.assertEqual(self.current(),m.render_units(m.ROOT,self.config))
        self.assertFalse(any('start' in c.args[0] or '--now' in c.args[0] for c in self.run.call_args_list))

    def test_no_upgrade_flag_leaves_old_installation_untouched(self):
        with self.assertRaisesRegex(SystemExit,'--upgrade'):m.main([])
        self.assertEqual(self.current(),self.old);self.run.assert_not_called()
    def test_active_service_blocks_all_changes(self):
        self.run.return_value=subprocess.CompletedProcess([],0,'LoadState=loaded\nActiveState=active\n','')
        with self.assertRaisesRegex(SystemExit,'Stop services'):m.main(['--upgrade'])
        self.assertEqual(self.current(),self.old)
        self.assertFalse((self.config/'backups').exists())
    def test_custom_base_unit_is_preserved(self):
        path=self.units/'onboardmimic.service';path.write_text(path.read_text().replace('--physical','--physical --default-speed 0.3'))
        before=self.current()
        with self.assertRaisesRegex(SystemExit,'Custom unit preserved'):m.main(['--upgrade'])
        self.assertEqual(self.current(),before)
    def test_relocated_dropin_requires_explicit_update(self):
        drop=self.units/'onboardmimic.service.d';drop.mkdir();(drop/'pose.conf').write_text('[Service]\nEnvironment=POSE="'+str(self.previous)+'/pose.json"\n')
        with self.assertRaisesRegex(SystemExit,'Drop-in references'):m.main(['--upgrade'])
        self.assertEqual(self.current(),self.old)
    def test_write_failure_restores_original_units(self):
        real=m.write_atomic
        def write(path,data,mode=0o644):
            if path.name=='onboardmimic-console.service' and data!=self.old[path.name]:raise OSError('disk error')
            real(path,data,mode)
        with patch.object(m,'write_atomic',side_effect=write):
            with self.assertRaisesRegex(OSError,'disk error'):m.main(['--upgrade'])
        self.assertEqual(self.current(),self.old)
    def test_new_installer_manifest_detects_manual_edits(self):
        m.main(['--upgrade'])
        path=self.units/'onboardmimic.service';path.write_text(path.read_text()+'# custom change\n')
        with self.assertRaisesRegex(SystemExit,'Custom unit preserved'):m.main(['--upgrade'])
        self.assertTrue(path.read_text().endswith('# custom change\n'))

    def test_previous_manifest_migrates_standard_dropins_and_retains_backups(self):
        import json
        old=m.render_units(self.previous,self.config)
        old={k:v.replace('Restart=always\nRestartSec=3','Restart=no') for k,v in old.items()}
        for name,data in old.items():(self.units/name).write_text(data)
        (self.config/'installed_services.json').write_text(json.dumps({'root':str(self.previous),'units':old}))
        folder=self.units/'onboardmimic-console.service.d';folder.mkdir()
        command=next(line for line in old['onboardmimic-console.service'].splitlines() if line.startswith('ExecStart='))
        original='[Service]\nExecStart=\n'+command.replace('--host 127.0.0.1','--host 0.0.0.0')+'\n'
        drop=folder/'listen.conf';drop.write_text(original)
        m.main(['--upgrade'])
        self.assertNotIn(str(self.previous),drop.read_text())
        self.assertIn(str(m.ROOT),drop.read_text())
        backup=next((self.config/'backups').iterdir())
        self.assertEqual((backup/folder.name/'listen.conf').read_text(),original)
        self.assertIn('Restart=always',self.current()['onboardmimic.service'])

class ManifestUpgradeTest(unittest.TestCase):
    setUp = UpgradeTest.setUp
    tearDown = UpgradeTest.tearDown

    def test_standard_overrides_follow_root_and_command_changes(self):
        import json
        for previous in (self.previous,m.ROOT):
            with self.subTest(previous=previous):
                old={name:text.replace(' --robot "unitree_g1" --backend "sonic"','')
                     for name,text in m.render_units(previous,self.config).items()}
                for name,text in old.items():(self.units/name).write_text(text)
                (self.config/'installed_services.json').write_text(json.dumps({'root':str(previous),'units':old}))
                for name in ('onboardmimic.service','onboardmimic-console.service'):
                    folder=self.units/(name+'.d');folder.mkdir(exist_ok=True)
                    command=next(line for line in old[name].splitlines() if line.startswith('ExecStart='))
                    if name=='onboardmimic.service':
                        command+=' --pose-status '+m.quote(previous/'onboardmimic/inputs/pico/runs/live/status.json')
                    else:
                        command=command.replace('--host 127.0.0.1','--host 0.0.0.0')
                    (folder/'standard.conf').write_text('[Service]\nExecStart=\n'+command+'\n')
                m.main(['--upgrade'])
                for name in ('onboardmimic.service','onboardmimic-console.service'):
                    migrated=(self.units/(name+'.d')/'standard.conf').read_text()
                    self.assertIn('--robot "unitree_g1" --backend "sonic"',migrated)
                    self.assertIn('-m onboardmimic.',migrated)
                self.assertIn(m.quote(m.ROOT/'onboardmimic/inputs/pico/runs/live/status.json'),
                              (self.units/'onboardmimic.service.d/standard.conf').read_text())
                self.assertIn('--host 0.0.0.0',
                              (self.units/'onboardmimic-console.service.d/standard.conf').read_text())
                self.assertEqual((self.config/'http.token').read_text(),'existing-secret-token')
                self.assertFalse(any('start' in call.args[0] or '--now' in call.args[0]
                                     for call in self.run.call_args_list))

if __name__=='__main__':unittest.main()
