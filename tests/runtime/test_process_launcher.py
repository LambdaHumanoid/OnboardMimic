import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
from onboardmimic.core.process_launcher import ProcessLauncher

LAUNCHER = str(Path(__file__).resolve().parents[2]/'onboardmimic/core/owned_exec.py')

class ProcessLauncherTests(unittest.TestCase):
    def test_real_http_thread_exit_preserves_owned_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp)/'ready'
            def spawn():
                return subprocess.Popen([sys.executable, LAUNCHER, str(os.getpid()), sys.executable,
                    '-c', 'import pathlib,time,sys;pathlib.Path(sys.argv[1]).touch();time.sleep(30)', str(ready)])
            launch = ProcessLauncher(spawn)
            children = []
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args): pass
                def do_GET(self):
                    children.append(launch())
                    deadline = time.monotonic()+5
                    while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)
                    self.send_response(200);self.end_headers();self.wfile.write(b'ok')
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            worker = threading.Thread(target=server.serve_forever);worker.start()
            try:
                with urlopen('http://127.0.0.1:%d/' % server.server_port, timeout=8) as r:
                    self.assertEqual(r.read(), b'ok')
                server.shutdown();server.server_close();worker.join()
                self.assertTrue(ready.exists())
                time.sleep(.1)
                self.assertIsNone(children[0].poll())
                self.assertTrue(launch.thread.is_alive())
            finally:
                server.shutdown();server.server_close();worker.join()
                for child in children:
                    if child.poll() is None: child.kill()
                    child.wait(timeout=3)
                launch.close()

    def test_spawn_error_does_not_end_owner_thread(self):
        def fail(): raise ValueError('spawn failed')
        launch = ProcessLauncher(fail)
        try:
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'spawn failed'): launch()
            self.assertTrue(launch.thread.is_alive())
        finally: launch.close()
        with self.assertRaises(RuntimeError): launch()

    def test_gateway_death_still_kills_worker_created_child(self):
        code = '''import os,sys,subprocess,time
from onboardmimic.core.process_launcher import ProcessLauncher
launch=ProcessLauncher(lambda:subprocess.Popen([sys.executable,sys.argv[1],str(os.getpid()),sys.executable,'-c','import time;print("ready",flush=True);time.sleep(30)'],stdout=subprocess.PIPE,text=True))
p=launch();assert p.stdout.readline().strip()=='ready';print(p.pid,flush=True);time.sleep(30)
'''
        parent = subprocess.Popen([sys.executable, '-c', code, LAUNCHER], stdout=subprocess.PIPE, text=True)
        child = None
        try:
            child = int(parent.stdout.readline())
            parent.kill();parent.wait(timeout=3)
            deadline = time.monotonic()+3
            while time.monotonic()<deadline:
                try:
                    if Path('/proc/%d/stat'%child).read_text().split()[2]=='Z': break
                except (FileNotFoundError, ProcessLookupError): break
                time.sleep(.01)
            else: self.fail('child survived runtime death')
        finally:
            if parent.poll() is None: parent.kill();parent.wait(timeout=3)
            parent.stdout.close()
            if child:
                try: os.kill(child, signal.SIGKILL)
                except ProcessLookupError: pass
