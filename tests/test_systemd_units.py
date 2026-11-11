"""Parse generated units with real systemd; never load or start a service."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('installer',ROOT/'scripts/install_services.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class UnitSyntaxTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd-analyze required')
    def test_generated_units_parse_without_path_warnings(self):
        with tempfile.TemporaryDirectory(prefix='sonic-unit-') as temp:
            root=Path(temp)/'sonic with spaces % literal'
            (root/'.venv/bin').mkdir(parents=True)
            (root/'.venv/bin/python').symlink_to(sys.executable)
            (root/'onboardmimic/backends/sonic/deploy/onboard').mkdir(parents=True)
            config=root/'config with spaces';config.mkdir()
            (config/'bridge.env').write_text('EXAMPLE=1\n')
            paths=[]
            for name,data in m.render_units(root,config).items():
                path=Path(temp)/name;path.write_text(data);paths.append(str(path))
            result=subprocess.run(['systemd-analyze','--user','verify',*paths],
                                  capture_output=True,text=True,env={**os.environ,'LC_ALL':'C'})
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotRegex(result.stderr,r'path is not absolute|WorkingDirectory=|EnvironmentFile=')

    def test_control_character_path_is_rejected(self):
        with self.assertRaises(ValueError):
            m.render_units(Path('/tmp/sonic\nExecStart=/bin/false'),Path('/tmp/config'))

if __name__=='__main__':unittest.main()
