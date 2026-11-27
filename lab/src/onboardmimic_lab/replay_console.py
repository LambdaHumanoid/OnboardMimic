"""Dataset browser, simulation jobs and optional OM-owned physical replay."""
import json
import os
import signal
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid
import tempfile

from .replay import prepare_plan, summary
from .dataset_io import episode_manifest
from .lerobot_storage import entries as lerobot_entries, find as find_lerobot


class ReplayConsole:
    def __init__(self, root=None, simulation_config=None, output=None, real=None):
        self.real = real
        self.root = Path(root).resolve() if root else None
        self.config = Path(simulation_config).resolve() if simulation_config else None
        self.output = Path(output).resolve() if output else (self.root/'replays' if self.root else None)
        self.lock = threading.RLock()
        self.job = None
        self.process = None
        self.closed = False

    def episode(self, name, require_ready=True, *, catalog=None):
        if not self.root: raise ValueError('Dataset directory is not configured')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', name):
            raise ValueError('Invalid episode ID')
        path = self.root/'episodes'/name
        if path.is_symlink() or path.resolve().parent != (self.root/'episodes').resolve():
            raise ValueError('Invalid episode path')
        if catalog is None:
            manifest = episode_manifest(path)
            saved = find_lerobot(path) is not None
        else:
            # One request-local snapshot: no persistent cache of recording readiness.
            saved = name in catalog
            if saved:
                from .lerobot_dataset import manifest_from_metadata
                manifest = manifest_from_metadata(catalog[name]['om'])
            else:
                manifest = json.loads((path/'manifest.json').read_text())
        if not isinstance(manifest, dict) or not isinstance(manifest.get('controller_spec'), dict) or not isinstance(manifest.get('counts', {}), dict):
            raise ValueError('Invalid episode manifest')
        if manifest.get('status') == 'raw_saved' and require_ready:
            raise ValueError('Episode alignment is not ready')
        if manifest.get('status') == 'saved' and not saved:
            raise ValueError('Only LeRobot v2.1 episodes can be replayed')
        if manifest.get('status') not in ('saved', 'raw_saved'): raise ValueError('Episode must be saved first')
        if manifest.get('controller_spec', {}).get('schema_version') != 2:
            raise ValueError('This page supports native v2 episodes only')
        return path, manifest

    def status(self):
        episodes = []; invalid = 0
        if self.root:
            from .tasks import episode_numbers
            saved_entries = lerobot_entries(self.root)
            catalog = {entry['om']['id']: entry for entry in saved_entries}
            numbers = episode_numbers(self.root, saved_entries=saved_entries)
            paths = [self.root/'episodes'/name for name in catalog]
            paths.extend(p for p in (self.root/'episodes').glob('*') if p.name not in catalog)
            for path in sorted(paths, reverse=True):
                try:
                    _, m = self.episode(path.name, require_ready=False, catalog=catalog)
                    spec = m['controller_spec']; counts = m.get('counts', {})
                    from .tasks import identity
                    task = identity(m)
                    episodes.append(dict(export_ready=m['status']=='saved', export=m.get('export', dict(state='complete')), id=path.name, task=task['description'], task_name=task['name'], task_id=task['id'], collector_name=m.get('collector_name',''),
                        created_realtime_ns=m.get('created_realtime_ns'), episode_index=numbers.get(path.name,{}).get('index'),
                        duration_s=m.get('duration_s') or counts.get('controller.jsonl', 0)*spec.get('period_ns', 0)/1e9,
                        frames=m.get('frames', counts.get('controller.jsonl', 0)),
                        execution_id=spec.get('execution_id', ''),
                        contract_sha256=m.get('contract_sha256', ''),
                        reference_space=spec.get('reference_space', ''),
                        test_fixture=spec.get('test_fixture', False)))
                except (ValueError, OSError, KeyError, TypeError): invalid += 1
        with self.lock:
            episodes.sort(key=lambda e:(e['task_id'], e['episode_index'] if e['episode_index'] is not None else -1))
            tasks = {}
            for episode in episodes:
                row = tasks.setdefault(episode['task_id'], dict(id=episode['task_id'], name=episode['task_name'], description=episode['task'], episode_count=0))
                row['episode_count'] += 1
            real = self.real.snapshot() if self.real else dict(configured=False)
            return dict(configured=self.root is not None, episodes=episodes, tasks=list(tasks.values()), invalid_episodes=invalid,
                        simulation_configured=bool(self.config and self.config.is_file()),
                        real_supported=bool(self.real and real['available']),
                        real=real,
                        job=dict(self.job) if self.job else None)

    def command(self, value):
        action = value.get('action')
        if action == 'simulate_stop':
            with self.lock:
                if not self.job or value.get('job_id') != self.job['id']:
                    raise ValueError('Simulation session changed')
                if self.job['state'] == 'running' and self.process and self.process.poll() is None:
                    self.job['state'] = 'stopping'
                    os.killpg(self.process.pid, signal.SIGTERM)
                return dict(self.job)
        if action in ('real_start', 'real_stop'):
            if not self.real: raise ValueError('Physical replay is not configured')
            return self.real.command(action[5:], value.get('owner'), value.get('plan_id'), value.get('run_id'))
        if action not in ('inspect', 'simulate', 'real_load'): raise ValueError('Unsupported replay action')
        name = value.get('episode'); path, _ = self.episode(name)
        segment = value.get('segment')
        if segment is not None and (type(segment) is not int or segment < 0): raise ValueError('Invalid segment')
        if action == 'real_load':
            if not self.real: raise ValueError('Physical replay is not configured')
            plan = prepare_plan(path, segment)
            return self.real.load(plan, name, plan['segment_index'])
        if action == 'inspect':
            from .reference_replay import segment_options
            options = segment_options(path)
            if segment is None and len(options) != 1:
                return dict(verified=False, segments=options)
            plan = prepare_plan(path, segment)
            return dict(verified=True, summary=summary(plan), segments=options)
        with self.lock:
            if self.closed: raise ValueError('Replay service is closing')
            if self.job and self.job['state'] in ('running','stopping'): raise ValueError('A simulation is already running')
            if not self.config or not self.config.is_file(): raise ValueError('Simulation environment is not configured on this host')
            job_id = uuid.uuid4().hex
            self.output.mkdir(parents=True, exist_ok=True)
            job_dir = self.output/job_id; job_dir.mkdir()
            args = [sys.executable, '-m', 'onboardmimic_lab', 'replay-sim', str(path),
                    '--config', str(self.config), '--output', str(job_dir/'simulation')]
            if segment is not None: args += ['--segment', str(segment)]
            log = (job_dir/'worker.log').open('wb')
            try:
                self.process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            finally: log.close()
            self.job = dict(id=job_id, episode=name, segment=segment, state='running', error=None, report=None, video=False)
            threading.Thread(target=self._wait, args=(self.process, job_dir), daemon=True).start()
            return dict(self.job)

    def _wait(self, process, directory):
        error = None
        try:
            code = process.wait(timeout=600)
            if code: error = (directory/'worker.log').read_text(errors='replace')[-3000:] or 'Simulation failed'
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait(); error = 'Simulation timed out after 600 seconds'
        report = None
        try: report = json.loads((directory/'simulation/result.json').read_text())
        except (OSError, ValueError): pass
        if not error and (not report or report.get('status') != 'completed'): error = 'Simulation did not produce a completed report'
        with self.lock:
            cancelled = self.job['state'] == 'stopping'
            self.job.update(state='stopped' if cancelled else 'failed' if error else 'completed', error=None if cancelled else error, report=report,
                            video=not error and (directory/'simulation/replay.mp4').is_file())
            (directory/'job.json').write_text(json.dumps(self.job, allow_nan=False, indent=2))

    def file(self, job_id, name):
        if name not in ('result.json', 'replay.mp4'): raise ValueError('Unsupported replay file')
        with self.lock:
            if not self.job or job_id != self.job['id'] or self.job['state'] == 'running':
                raise ValueError('Replay result is not available')
            path = self.output/job_id/'simulation'/name
            if not path.is_file(): raise ValueError('Replay file is not available')
            return path

    def preview(self, name):
        path, manifest = self.episode(name)
        if find_lerobot(path) is not None:
            from .lerobot_dataset import verify
            value=verify(path)
            return self.root/value['paths']['ego_view.mp4']
        raise ValueError('Only LeRobot v2.1 episodes can be replayed')

    def close(self):
        with self.lock:
            self.closed = True
            if self.process and self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
