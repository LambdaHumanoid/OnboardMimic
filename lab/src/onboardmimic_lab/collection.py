"""Collector-owned episode lifecycle; read-only subscribers, no motion commands."""
import fcntl
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from .dataset import write_json
from .adapters.capture import capture_adapter
from .tasks import Tasks, identity, episode_numbers, reserve_episode


def load_config(path):
    path = Path(path).resolve()
    return validate_config(json.loads(path.read_text()), path.parent)


def validate_config(value, base=None):
    from .adapters.capture import validate_capture_config
    return validate_capture_config(value, base)


class Collector:
    def __init__(self, config, runtime_status, worker=None, exporter=None):
        self.config = dict(config)
        self.runtime_status = runtime_status
        self.adapter = capture_adapter(config)
        self.worker = worker or (lambda raw: self.adapter.record(self, raw))
        self.root = Path(config["root"])
        for name in ("pending", "episodes", "discarded"):
            (self.root/name).mkdir(parents=True, exist_ok=True)
        self.process_lock = (self.root/".collector.lock").open("a")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.process_lock.close()
            raise ValueError("another collector owns this dataset root")
        self.lock = threading.RLock()
        self.tasks = Tasks(self.root)
        self.task_id = None
        selection = self.root/'selected-task.json'
        if selection.exists():
            self.task_id = json.loads(selection.read_text()).get('task_id')
        self.stop_event = threading.Event()
        self.thread = None
        self.state, self.error, self.episode_id = "idle", "", None
        self.counts, self.started = {}, None
        self.incidents = None
        self.camera = None
        if worker is None and self.adapter.id in ('reference-v1', 'sonic-native-v2'):
            from .camera_ingress import CameraIngress
            self.camera = CameraIngress(config['camera_port'])
        # Recovered unfinished captures are never silently accepted for training.
        for directory in (self.root/"pending").iterdir():
            if not directory.is_dir():
                continue
            manifest = directory/"manifest.json"
            if manifest.exists():
                value = json.loads(manifest.read_text())
                if value.get("status") == "recording":
                    value.update(status="failed", error="collector exited during capture")
                    write_json(manifest, value)
                elif value.get("status") == "raw_saved":
                    directory.rename(self.root/"episodes"/directory.name)
                elif value.get("status") == "saved":
                    value.update(status="review", error="save promotion interrupted; retry save")
                    write_json(manifest, value)

        from .export_jobs import ExportJobs
        self.exports = ExportJobs(self.root, lambda: self.state in ("arming", "recording", "stopping"), exporter)

    def status(self):
        with self.lock:
            numbers = episode_numbers(self.root)
            pending = []
            for d in sorted((self.root/"pending").iterdir()):
                if d.is_dir() and (d/"manifest.json").exists():
                    m = json.loads((d/"manifest.json").read_text())
                    pending.append(dict(id=d.name, status=m["status"], task=m.get("task", ""), episode_index=numbers.get(d.name,{}).get('index')))
            return dict(enabled=True, capture_adapter=self.adapter.id, state=self.state, error=self.error, episode_id=self.episode_id,
                        discontinuities=self.incidents.snapshot() if self.incidents else dict(count=0,recent=[]),
                        counts=dict(self.counts), pending=pending, exports=self.exports.status(), tasks=self.tasks.list(), task_id=self.task_id,
                        episode_index=numbers.get(self.episode_id,{}).get('index'),
                        camera=self.camera.status() if self.camera else dict(ready=False, error='not_monitored'),
                        elapsed_s=round(time.monotonic()-self.started, 1) if self.started else 0)

    def command(self, request):
        action = request.get("action")
        with self.lock:
            if action == "start":
                if self.state not in ("idle", "saved", "discarded") or self.status()["pending"]:
                    raise ValueError("save/discard the pending episode before starting another")
                person = request.get('collector_name', '')
                if not isinstance(person, str) or len(person) > 100:
                    raise ValueError('invalid collector name')
                adapter_metadata = self.adapter.metadata(self.config, self.runtime_status())
                if shutil.disk_usage(self.root).free < 2*1024**3:
                    raise ValueError("at least 2 GiB free disk space is required")
                task = self.tasks.resolve(request)
                self.task_id = task['id']
                write_json(self.root/'selected-task.json', dict(task_id=self.task_id))
                self.episode_id = uuid.uuid4().hex
                episode_index = reserve_episode(self.root, self.task_id, self.episode_id)
                metadata = dict(adapter_metadata, id=self.episode_id, task=task['description'],
                                episode_index=episode_index,
                                task_id=self.task_id, task_name=task['name'], task_description=task['description'], collector_name=person.strip(), created_realtime_ns=time.time_ns(),
                                recorder_boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
                self.state, self.error, self.counts, self.started = "arming", "", {}, None
                self.incidents = None
                self.stop_event.clear()
                self.thread = threading.Thread(target=self._run, args=(metadata,), daemon=True)
                self.thread.start()
            elif action == "stop":
                if self.state not in ("arming", "recording", "stopping"):
                    raise ValueError("no active capture")
                self.state = "stopping"
                self.stop_event.set()
            elif action == "select":
                if self.state not in ("idle", "saved", "discarded"):
                    raise ValueError("another episode is selected")
                episode_id = request.get("id", "")
                if not isinstance(episode_id, str) or len(episode_id) != 32 or any(c not in "0123456789abcdef" for c in episode_id):
                    raise ValueError("invalid episode id")
                path = self.root/"pending"/episode_id/"manifest.json"
                value = json.loads(path.read_text())
                self.episode_id, self.state = episode_id, value["status"]
                self.task_id = identity(value)['id']
                self.error, self.counts = value.get("error") or "", value.get("counts", {})
            elif action == "save":
                if self.state != "review":
                    raise ValueError("only a completed capture can be saved")
                self.state, self.error = "saving", ""
                self.thread = threading.Thread(target=self._save, daemon=True)
                self.thread.start()
            elif action == "retry_export":
                self.exports.retry(request.get("id"))
            elif action == "discard":
                if self.state not in ("review", "failed"):
                    raise ValueError("stop capture before discarding")
                path = self.root/"pending"/self.episode_id
                if path.exists():
                    value = json.loads((path/"manifest.json").read_text())
                    value["status"] = "discarded"
                    write_json(path/"manifest.json", value)
                    path.rename(self.root/"discarded"/self.episode_id)
                self.state, self.started = "discarded", None
            else:
                raise ValueError("unknown collection action")
            return self.status()

    def _run(self, metadata):
        raw = None
        error = None
        try:
            raw = self.adapter.raw_episode(self.root/"pending"/self.episode_id, metadata)
            if hasattr(raw, 'incidents'):
                from .capture_incidents import CaptureIncidents
                diagnostics=CaptureIncidents(self.root/'diagnostics'/self.episode_id,metadata)
                raw.incidents=diagnostics
                with self.lock:self.incidents=diagnostics
            self.worker(raw)
        except Exception as exc:
            error = str(exc)
            if raw and getattr(raw, "incidents", None) and raw.previous:
                raw.incidents.submit(raw.previous,None,time.monotonic_ns(),reason=error,
                                     context=dict(raw.diagnostic_context))
        finally:
            if raw:
                try:
                    raw.finish(error)
                except Exception as exc:
                    error = "finalization failed: " + str(exc)
            if raw and getattr(raw, "incidents", None):raw.incidents.close()
            with self.lock:
                self.state = "failed" if error else "review"
                self.error = error or ""
                self.started = None

    def _save(self):
        # Raw finish already fsyncs and hashes every stream. Verify before promotion;
        # video decoding/alignment is not on this critical path.
        try:
            from .dataset import verify_raw
            episode_id = self.episode_id
            path = self.root/"pending"/episode_id
            value = verify_raw(path)
            value.update(status="raw_saved", export=dict(state="queued"))
            write_json(path/"manifest.json", value)
            with self.lock:
                destination = self.root/"episodes"/episode_id
                path.rename(destination)
                # Persist the rename before acknowledging raw data saved.
                import os
                try:
                    for directory in (self.root/"pending", self.root/"episodes"):
                        fd = os.open(directory, os.O_RDONLY|os.O_DIRECTORY)
                        try: os.fsync(fd)
                        finally: os.close(fd)
                except Exception:
                    destination.rename(path)
                    raise
                self.exports.enqueue(destination)
                self.state = "saved"
        except Exception as exc:
            with self.lock:
                self.state, self.error = "review", str(exc)

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=130)
            if self.thread.is_alive():
                raise RuntimeError("collector worker has not stopped")
        self.exports.close()
        self.process_lock.close()
        if self.camera:self.camera.close()
