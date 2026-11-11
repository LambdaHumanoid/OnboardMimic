"""Exercise the installer's test command across cwd changes, without hardware."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TestEntrypointTest(unittest.TestCase):
    def test_relative_absolute_and_path_interpreters_keep_venv(self):
        with tempfile.TemporaryDirectory(prefix='sonic entrypoint ') as tmp:
            root = Path(tmp)
            (root/'scripts').mkdir()
            shutil.copy2(ROOT/'scripts/test.sh', root/'scripts/test.sh')
            venv = root/'.venv'
            subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)], check=True)
            recorder = '''import json, os, sys
from pathlib import Path
assert Path(sys.prefix) == Path(os.environ['EXPECTED_VENV']), sys.prefix
with open(os.environ['CHECK_LOG'], 'a') as log:
    log.write(json.dumps(STAGE) + '\\n')
'''
            for stage, folder in (
                ('core', 'tests/runtime'), ('release', 'tests'),
                ('console', 'tests/console'),
                ('pico', 'tests/pico'), ('sonic', 'tests/sonic'),
            ):
                path = root/folder
                path.mkdir(parents=True, exist_ok=True)
                (root/'tests/__init__.py').touch()
                (path/'__init__.py').touch()
                (path/'test_probe.py').write_text(
                    'import unittest\nSTAGE = '+repr(stage)+
                    '\nclass Probe(unittest.TestCase):\n    def test_environment(self):\n'+
                    textwrap.indent(recorder, '        '))
            (root/'onboardmimic/backends/sonic/deploy/onboard').mkdir(parents=True)
            (root/'onboardmimic/backends/sonic/deploy/onboard/validate_safety_source.py').write_text(
                "STAGE = 'safety'\n"+recorder)
            log = root/'checks.jsonl'
            env = dict(os.environ, EXPECTED_VENV=str(venv), CHECK_LOG=str(log),
                       PATH=str(venv/'bin')+os.pathsep+os.environ['PATH'])
            for python, mode in (('.venv/bin/python', '--core'),
                                 (str(venv/'bin/python'), '--all'), ('python', '--all')):
                with self.subTest(python=python, mode=mode):
                    log.write_text('')
                    result = subprocess.run(['bash', 'scripts/test.sh', mode], cwd=root,
                                            env=dict(env, PYTHON=python), capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    stages = [json.loads(line) for line in log.read_text().splitlines()]
                    self.assertEqual(stages, ['core', 'safety', 'release', 'console']+
                                     (['pico', 'sonic'] if mode == '--all' else []))
