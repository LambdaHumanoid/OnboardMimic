"""The distributed user surface uses the platform identity, not past project names."""
from pathlib import Path
import unittest
from scripts.install_services import render_units
from onboardmimic.inputs.pico.install_service import render

ROOT=Path(__file__).resolve().parents[1]
class PublicIdentityTests(unittest.TestCase):
    def test_document_and_console_titles_use_platform_identity(self):
        self.assertIn('OnboardMimic',(ROOT/'README.md').read_text())
        for filename in ('index.html','mobile.html'):
            self.assertIn('<title>OnboardMimic',(ROOT/'onboardmimic/console/static'/filename).read_text())
    def test_fresh_user_and_receiver_units_use_current_namespace(self):
        root=Path('/opt/OnboardMimic');config=Path('/home/example/.config/onboardmimic')
        units=render_units(root,config)
        self.assertEqual(len(units),7)
        for name,text in units.items():
            self.assertTrue(name.startswith('onboardmimic'))
            self.assertIn('Description=OnboardMimic',text)
        receiver,dropin=render(root,config)
        self.assertIn('Description=OnboardMimic',receiver)
        self.assertIn('-m onboardmimic.core.runtime',dropin)
        self.assertIn('onboardmimic-pico-receiver',receiver)

if __name__=='__main__':unittest.main()
