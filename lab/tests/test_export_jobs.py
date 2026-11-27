"""Software-only collection/export concurrency and recovery regressions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

from onboardmimic_lab.collection import Collector
from onboardmimic_lab.dataset import export_episode, write_json
from onboardmimic_lab.export_jobs import ExportJobs
from onboardmimic_lab.replay_console import ReplayConsole
from test_collection import fixture_config, make_video, fill, active
from export_test_helpers import wait_export


def until(predicate, timeout=10):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        value = predicate()
        if value: return value
        time.sleep(.01)
    raise AssertionError('condition timed out')


class ExportDeadlineTest(unittest.TestCase):
    def test_twenty_minutes_active_time_excludes_capture_pause(self):
        # Test the real supervision loop with a fake process and virtual clock.
        for times,busy,expires in [([0,601,1199],[False,False],False),
                                   ([0,1201],[False],True),
                                   ([0,1,2001,2600],[True,False,False],False)]:
            with self.subTest(times=times), tempfile.TemporaryDirectory() as folder:
                path=Path(folder);jobs=ExportJobs.__new__(ExportJobs)
                jobs.exporter=None;jobs.stop=Mock();jobs.stop.wait.return_value=False
                jobs.capture_busy=Mock(side_effect=busy);jobs.lock=threading.RLock()
                jobs.jobs={path.name:{}}
                process=Mock(pid=123,returncode=0)
                process.poll.side_effect=[None]*len(busy)+[0,0]
                with patch('onboardmimic_lab.export_jobs.subprocess.Popen',return_value=process), \
                     patch('onboardmimic_lab.export_jobs.time.monotonic',side_effect=times), \
                     patch('onboardmimic_lab.export_jobs.os.killpg'):
                    if expires:
                        with self.assertRaises(TimeoutError):jobs._execute(path)
                    else:jobs._execute(path)


class ExportQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.video = make_video(self.root/'input.ts')
        for name in ('encoder','decoder'): (self.root/name).write_bytes(b'fixture')
        self.config = fixture_config(self.root/'data')
        self.collector = None

    def tearDown(self):
        if self.collector: self.collector.close()

    def create(self, exporter=None):
        def worker(raw):
            fill(raw, self.video); self.collector.stop_event.wait(3)
        self.collector = Collector(self.config, active, worker=worker, exporter=exporter)
        return self.collector

    def capture_save(self):
        c = self.collector
        result = c.command(dict(action='start', task_name='test'))
        c.command(dict(action='stop')); until(lambda:c.status()['state']=='review')
        c.command(dict(action='save')); until(lambda:c.status()['state']=='saved')
        return self.root/'data/episodes'/result['episode_id']

    def test_slow_export_does_not_block_next_capture_or_overwrite_its_state(self):
        gate = threading.Event(); entered = threading.Event()
        def slow(path):
            entered.set(); gate.wait(5); return export_episode(path)
        c = self.create(slow)
        try:
            first = self.capture_save(); self.assertTrue(entered.wait(2))
            self.assertEqual(json.loads((first/'manifest.json').read_text())['status'],'raw_saved')
            second = c.command(dict(action='start', task_name='next'))
            self.assertNotEqual(second['episode_id'],first.name)
            gate.set(); wait_export(first)
            self.assertEqual(c.status()['episode_id'],second['episode_id'])
            self.assertEqual(c.status()['state'],'arming')
            c.command(dict(action='stop')); until(lambda:c.status()['state']=='review')
            c.command(dict(action='discard'))
        finally: gate.set()

    def test_failure_keeps_raw_and_retry_is_independent_of_current_episode(self):
        attempts=[]
        def fail_once(path):
            attempts.append(path)
            if len(attempts)==1: raise ValueError('fixture export failure')
            return export_episode(path)
        c=self.create(fail_once); path=self.capture_save()
        until(lambda:c.exports.status()[0]['state']=='failed')
        self.assertTrue((path/'camera.ts').exists())
        c.command(dict(action='start',task_name='next'))
        c.command(dict(action='retry_export',id=path.name))
        time.sleep(.2); self.assertEqual(len(attempts),1)
        c.command(dict(action='stop'));until(lambda:c.state=='review')
        c.command(dict(action='discard'));wait_export(path)
        self.assertEqual(len(attempts),2)
        self.assertEqual(c.state,'discarded')

    def test_real_worker_pauses_during_capture_and_recovers_after_shutdown(self):
        c=self.create(); real_popen=subprocess.Popen
        def slow_worker(args, **kw):
            code='import time; time.sleep(30)'
            return real_popen([sys.executable,'-c',code],**kw)
        with patch('onboardmimic_lab.export_jobs.subprocess.Popen',side_effect=slow_worker):
            path=self.capture_save();until(lambda:c.exports.process is not None)
            pid=c.exports.process.pid
            c.command(dict(action='start',task_name='next'))
            until(lambda:any(j['state']=='paused' for j in c.exports.status()))
            # Linux process state T proves the worker was actually suspended.
            self.assertEqual(Path('/proc/%s/stat'%pid).read_text().split(') ')[1].split()[0],'T')
            c.close();self.collector=None
            self.assertFalse(Path('/proc/%s'%pid).exists())
        self.assertEqual(json.loads((path/'manifest.json').read_text())['export']['state'],'queued')
        c=self.create();wait_export(path)
        self.assertEqual(c.state,'idle')

    def test_worker_error_is_visible_and_does_not_destroy_raw(self):
        c=self.create()
        # Keep queued export from starting, then corrupt a raw file after save.
        c.exports.capture_busy=lambda:True
        path=self.capture_save(); original=(path/'camera.ts').read_bytes()
        (path/'camera.ts').write_bytes(b'corrupt')
        c.exports.capture_busy=lambda:False
        until(lambda:any(j['state']=='failed' for j in c.exports.status()))
        self.assertIn('integrity',c.exports.status()[0]['error'])
        self.assertEqual(c.state,'saved')
        (path/'camera.ts').write_bytes(original)
        c.command(dict(action='retry_export',id=path.name));wait_export(path)

    def test_raw_saved_episode_is_visible_but_cannot_be_replayed(self):
        root=self.root/'browse';path=root/'episodes'/('a'*32);path.mkdir(parents=True)
        write_json(path/'manifest.json',dict(status='raw_saved',controller_spec=dict(schema_version=2),counts={},export=dict(state='queued'),episode_index=0))
        replay=ReplayConsole(root)
        status=replay.status();self.assertEqual(status['invalid_episodes'],0)
        self.assertFalse(status['episodes'][0]['export_ready'])
        with self.assertRaisesRegex(ValueError,'alignment is not ready'):
            replay.command(dict(action='inspect',episode=path.name))

    def test_parent_crash_kills_export_worker_and_its_decoder(self):
        c=self.create();c.exports.capture_busy=lambda:True
        path=self.capture_save()
        fake=self.root/'bin';fake.mkdir();probe=fake/'ffprobe'
        probe.write_text('#!'+sys.executable+'\nimport os,time\nfrom pathlib import Path\nPath(os.environ["PROBE_PID"]).write_text(str(os.getpid()))\ntime.sleep(30)\n')
        probe.chmod(0o755)
        child_pid=self.root/'child.pid';probe_pid=self.root/'probe.pid'
        script='''import os,subprocess,sys,time
from pathlib import Path
env=dict(os.environ,OM_EXPORT_WORKER='1',OM_EXPORT_PARENT=str(os.getpid()))
p=subprocess.Popen([sys.executable,'-m','onboardmimic_lab.export_jobs',sys.argv[1]],env=env,start_new_session=True)
Path(sys.argv[2]).write_text(str(p.pid))
time.sleep(30)
'''
        parent=subprocess.Popen([sys.executable,'-c',script,str(path),str(child_pid)],
            env=dict(os.environ,PATH=str(fake)+':'+os.environ['PATH'],PROBE_PID=str(probe_pid)),
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            until(probe_pid.exists)
            worker=int(child_pid.read_text());decoder=int(probe_pid.read_text())
            parent.kill();parent.wait()
            def stopped(pid):
                stat=Path('/proc/%s/stat'%pid)
                return not stat.exists() or stat.read_text().split(') ')[1].split()[0]=='Z'
            until(lambda:stopped(worker) and stopped(decoder))
        finally:
            if parent.poll() is None:parent.kill();parent.wait()
            if child_pid.exists():
                try:os.killpg(int(child_pid.read_text()),9)
                except ProcessLookupError:pass
