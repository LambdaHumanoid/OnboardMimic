"""Standalone Lab must neither locate nor import OM."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

class IndependentInstallTest(unittest.TestCase):
    def test_services_and_cli_without_om_checkout(self):
        source=Path(__file__).resolve().parents[1]/'src/onboardmimic_lab'
        with tempfile.TemporaryDirectory() as directory:
            shutil.copytree(source,Path(directory)/'onboardmimic_lab')
            code="""import sys, importlib.abc
sys.path.insert(0,sys.argv[1])
class NoOM(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='onboardmimic' or fullname.startswith('onboardmimic.'):
   raise AssertionError('Lab attempted to import OM: '+fullname)
sys.meta_path.insert(0,NoOM())
import onboardmimic_lab.console, onboardmimic_lab.collector_service
from onboardmimic_lab.cli import main
sys.argv=['onboardmimic-lab','--help'];main()
"""
            subprocess.run([sys.executable,'-I','-c',code,directory],cwd=directory,check=True,capture_output=True)

    def test_capture_requires_explicit_supported_adapter(self):
        from onboardmimic_lab.collection import validate_config
        from test_collection import fixture_config
        with tempfile.TemporaryDirectory() as directory:
            config=fixture_config(directory)
            self.assertEqual(validate_config(config)['capture_adapter'],'reference-v1')
            for adapter in (None,'sonic','unknown'):
                invalid=dict(config,capture_adapter=adapter)
                with self.assertRaises(ValueError):validate_config(invalid)
