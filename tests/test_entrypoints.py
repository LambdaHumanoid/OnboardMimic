"""Documented script commands must resolve their own package from any cwd."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class EntrypointTest(unittest.TestCase):
    def test_help_outside_repository_does_not_import_an_unrelated_scripts_package(self):
        with tempfile.TemporaryDirectory(prefix='onboardmimic caller ') as directory:
            for command in ('scripts/setup.py','scripts/install_services.py',
                            'scripts/install_pico_service.py','scripts/install_ros_dependencies.py',
                            'scripts/build_prebuilt_release.py','scripts/check_prebuilt.py',
                            'scripts/control.py','download_from_hf.py'):
                with self.subTest(command=command):
                    result=subprocess.run([sys.executable,str(ROOT/command),'--help'],cwd=directory,
                                          capture_output=True,text=True,timeout=15)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertIn('usage:',result.stdout)
            self.assertEqual(list(Path(directory).iterdir()),[])
