"""Durable, single-worker export queue. No robot/controller interfaces."""
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import threading
import time

from .dataset import digest, export_episode, verify_raw, write_json
from .dataset_io import episode_manifest
from .lerobot_storage import find as find_lerobot


def finish_export(path, exporter=export_episode):
    """Commit only verified output; interrupted promotion can safely be retried."""
    path = Path(path)
    started = time.monotonic()
    if find_lerobot(path) is not None:
        from .lerobot_dataset import cleanup
        cleanup(path)
        return
    initial = episode_manifest(path)
    if initial.get('controller_spec', {}).get('schema_version') == 2 and initial['controller_spec'].get('backend_id')=='sonic':
        from .lerobot_dataset import export
        export(path)
        return
    if (path/'aligned/episode.json').exists():
        verify_raw(path)
        summary = json.loads((path/'aligned/episode.json').read_text())
        for name, expected in summary['files'].items():
            target = path/'aligned'/name
            if (path/'aligned').resolve() not in target.resolve().parents or digest(target) != expected:
                raise ValueError('aligned export integrity failed')
    else:
        # Only abandoned generated output is removed; original streams stay intact.
        for partial in path.glob('.export-*'):
            if re.fullmatch(r'\.export-[0-9a-f]{32}', partial.name) and partial.is_dir() and not partial.is_symlink():
                shutil.rmtree(partial)
        summary = exporter(path)
    value = episode_manifest(path)
    if value.get('format') == 'onboardmimic_lab_multimodal_v2':
        from .raw_storage import compact_raw
        compact_raw(path)
        value = episode_manifest(path)
    value.update(status='saved', aligned_rows=summary['rows'],
                 aligned_manifest=digest(path/'aligned/episode.json'),
                 export=dict(state='complete', elapsed_s=round(time.monotonic()-started, 3)))
    write_json(path/'manifest.json', value)


class ExportJobs:
    def __init__(self, root, capture_busy, exporter=None):
        self.root = Path(root); self.capture_busy = capture_busy; self.exporter = exporter
        from .lerobot_storage import recover
        recover(self.root)
        self.lock = threading.RLock(); self.stop = threading.Event(); self.jobs = {}; self.process = None
        for path in sorted((self.root/'episodes').iterdir()):
            if not path.is_dir() or not ((path/'manifest.json').exists() or (path/'episode.json').exists()): continue
            value = episode_manifest(path)
            if find_lerobot(path) is not None and (path/'manifest.json').exists():
                from .lerobot_dataset import cleanup
                try: cleanup(path)
                except (ValueError, OSError): pass  # keep originals on corrupt output; do not block all capture
            if value.get('status') != 'raw_saved': continue
            job = value.get('export', {})
            if job.get('state') != 'failed':
                value['export'] = dict(state='queued', queued_at_ns=job.get('queued_at_ns', time.time_ns()))
                write_json(path/'manifest.json', value)
            self._remember(path, value)
        self.thread = threading.Thread(target=self._run, daemon=True); self.thread.start()

    def _remember(self, path, value):
        queued_at = self.jobs.get(path.name, {}).get('queued_at_ns', time.time_ns())
        job = dict(value.get('export', {}))
        job.setdefault('queued_at_ns', queued_at)
        self.jobs[path.name] = dict(id=path.name, episode_index=value.get('episode_index'),
            task_name=value.get('task_name', value.get('task', '')), **job)

    def enqueue(self, path):
        with self.lock:
            value = episode_manifest(path)
            value['export'].setdefault('queued_at_ns', time.time_ns())
            write_json(path/'manifest.json', value)
            self._remember(path, value)

    def retry(self, name):
        if not isinstance(name, str) or not re.fullmatch('[0-9a-f]{32}', name):
            raise ValueError('invalid episode id')
        with self.lock:
            if self.jobs.get(name, {}).get('state') != 'failed':
                raise ValueError('only a failed export can be retried')
            path = self.root/'episodes'/name
            value = episode_manifest(path)
            value['export'] = dict(state='queued', queued_at_ns=time.time_ns())
            write_json(path/'manifest.json', value)
            self.jobs[name]['queued_at_ns'] = time.time_ns()
            self._remember(path, value)

    def status(self):
        with self.lock:
            active = [dict(j) for j in self.jobs.values() if j['state'] != 'complete']
            recent = [dict(j) for j in self.jobs.values() if j['state'] == 'complete'][-3:]
            from .export_progress import read_progress
            for job in active:
                if job['state'] in ('running', 'paused'):
                    progress = read_progress(self.root/'episodes'/job['id'])
                    if progress is not None: job['progress'] = progress
            return active + recent

    def _execute(self, path):
        if self.exporter is not None:  # injectable software-test worker
            finish_export(path, self.exporter); return
        env = dict(os.environ, OM_EXPORT_WORKER='1', OM_EXPORT_PARENT=str(os.getpid()), OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
        with (path/'export.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, '-m', 'onboardmimic_lab.export_jobs', str(path)],
                stdout=log, stderr=log, env=env, start_new_session=True)
        self.process = process; paused = False; active_s = 0.; paused_s = 0.; last = time.monotonic()
        try:
            while process.poll() is None:
                if self.stop.wait(.1): raise InterruptedError('export worker stopped')
                now = time.monotonic()
                if not paused: active_s += now-last
                else: paused_s += now-last
                last = now
                busy = self.capture_busy()
                if busy != paused:
                    try: os.killpg(process.pid, signal.SIGSTOP if busy else signal.SIGCONT)
                    except ProcessLookupError: pass
                    paused = busy
                    with self.lock: self.jobs[path.name]['state'] = 'paused' if paused else 'running'
                if active_s > 1200: raise TimeoutError('export timed out')
            if process.returncode: raise ValueError('export worker failed; see export.log')
        finally:
            if process.poll() is None:
                for sig in (signal.SIGCONT, signal.SIGTERM):
                    try: os.killpg(process.pid, sig)
                    except ProcessLookupError: pass
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait()
            self.process = None
            # Service journal survives successful raw-episode cleanup, unlike export.log.
            print(json.dumps(dict(event='data_processing_worker_finished', episode_id=path.name,
                active_s=active_s, paused_s=paused_s, returncode=process.returncode)), flush=True)

    def _run(self):
        while not self.stop.wait(.1):
            if self.capture_busy(): continue
            with self.lock:
                name = next((n for n,j in self.jobs.items() if j['state'] == 'queued'), None)
                if name is None: continue
                path = self.root/'episodes'/name
                (path/'processing-progress.json').unlink(missing_ok=True)
                value = episode_manifest(path)
                value['export'] = dict(state='running', queued_at_ns=self.jobs[name]['queued_at_ns'])
                write_json(path/'manifest.json', value)
                self._remember(path, value)
                print(json.dumps(dict(event='data_processing_started', episode_id=name,
                    queue_wait_s=max(0., (time.time_ns()-self.jobs[name]['queued_at_ns'])/1e9))), flush=True)
            try:
                self._execute(path)
            except Exception as exc:
                value = episode_manifest(path)
                # Never downgrade an already committed result during shutdown.
                if value.get('status') != 'saved' and (self.stop.is_set() or value.get('export', {}).get('state') != 'failed'):
                    value['export'] = dict(state='queued' if self.stop.is_set() else 'failed', error=str(exc),
                                          queued_at_ns=self.jobs[name]['queued_at_ns'])
                    write_json(path/'manifest.json', value)
            with self.lock:
                self._remember(path, episode_manifest(path))
                found = find_lerobot(path)
                if found is not None and found[1]['om'].get('processing'):
                    print(json.dumps(dict(event='data_processing_stages', episode_id=name,
                        **found[1]['om']['processing'])), flush=True)

    def close(self):
        self.stop.set(); self.thread.join(timeout=10)
        if self.thread.is_alive(): raise RuntimeError('export worker has not stopped')


if __name__ == '__main__':
    os.nice(10)
    if os.environ.get('OM_EXPORT_WORKER') == '1':
        # The supervisor creates a dedicated session; kill its whole process
        # group on parent death, including ffmpeg, so restart cannot orphan work.
        import ctypes
        if os.getpgrp() != os.getpid(): raise RuntimeError('export requires an isolated process group')
        signal.signal(signal.SIGTERM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
        if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0):
            raise OSError('cannot install export parent-death guard')
        if os.getppid() != int(os.environ['OM_EXPORT_PARENT']):
            os.killpg(os.getpgrp(), signal.SIGKILL)
    try: finish_export(Path(sys.argv[1]))
    except Exception as exc:
        path = Path(sys.argv[1]); value = episode_manifest(path)
        if value.get('status') != 'saved':
            value['export'] = dict(state='failed', error=str(exc)); write_json(path/'manifest.json', value)
        raise
