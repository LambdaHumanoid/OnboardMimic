import importlib.util
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
import onboardmimic.inputs.pico.install_service as installer

class PicoServiceTests(unittest.TestCase):
    def test_portable_receive_only_service(self):
        unit, dropin = installer.render(Path('/home/new user/OnboardMimic'), Path('/home/new user/.config/onboardmimic'))
        self.assertEqual(unit.count('--mount '), 4)
        self.assertIn('--cap-drop ALL', unit)
        self.assertIn('PICO_RECORD=0', unit)
        self.assertNotIn('--privileged', unit)
        self.assertNotIn('http.token', unit)
        self.assertIn('--pose-status "/home/new user/', dropin)
        self.assertIn('ExecStart=\nExecStart=', dropin)
        self.assertIn('--physical --token-file', dropin)
    def test_ambiguous_mount_path_rejected(self):
        with self.assertRaises(ValueError):
            installer.render(Path('/home/comma,name/repo'), Path('/tmp/config'))


class DeviceMigrationTest(unittest.TestCase):
    def test_preserves_device_ids_and_never_overwrites_target(self):
        import tempfile
        from onboardmimic.inputs.pico.install_service import preserve_devices
        for relative in ('onboardmimic/inputs/pico/devices.json',):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                source=root/'old'/relative
                source.parent.mkdir(parents=True);source.write_text('{"trackers":["test-only"]}')
                target=root/'new/devices.json'
                preserve_devices(root/'old',target)
                self.assertEqual(target.read_text(),source.read_text())
                target.write_text('{"custom":true}')
                preserve_devices(root/'old',target)
                self.assertEqual(target.read_text(),'{"custom":true}')
