"""Isolated telemetry computation; only bounded execution evidence crosses IPC."""
import json
import gc
import multiprocessing
import socket
import time

from .receiver import Receiver

# No input/history/action arrays are needed for admission or replay progress.
EVIDENCE_KEYS = ('timestamp_monotonic_ns', 'reference_evidence', 'contract_sha256',
                 'session_id', 'index', 'output_commit', 'replay', 'native_mode', 'token_source')


def _worker(endpoint, ports, export_spec):
    # This spawned process handles owned JSON trees and bounded containers.
    # Reference counting still releases them immediately. Keep automatic cyclic
    # heap scans off the receipt deadline; audit/publication have their own GC.
    # Never apply this policy to the shared control runtime or plain Receiver.
    was_enabled=gc.isenabled()
    gc.disable()
    try:_receive(endpoint,ports,export_spec)
    finally:
        endpoint.close()
        if was_enabled:gc.enable()


def _receive(endpoint, ports, export_spec):
    receiver = Receiver(*ports, export_spec=export_spec, isolate_audit=True)
    endpoint.setblocking(False)
    previous = None
    last_sent = 0.
    exported = None

    def notify():
        nonlocal previous, last_sent, exported
        j = receiver.join
        key = (id(j.spec), id(j.last), j.failed, j.generation, j.writer_generation, receiver.error)
        now = time.monotonic()
        if key == previous and now-last_sent < .1:
            return
        value = dict(diagnostics=dict(receiver.diagnostics), ipc_sent_ns=time.monotonic_ns(), sessions=j.sessions, generation=j.generation,
                     writer_generation=j.writer_generation, failed=j.failed,
                     receiver_error=receiver.error, last=None if j.last is None else {k:j.last[k] for k in EVIDENCE_KEYS})
        if j.spec is not exported:
            value['spec'] = j.spec
        payload = json.dumps(value, allow_nan=False, separators=(',', ':')).encode()
        # Losing evidence is a telemetry failure, never a silently skipped replay frame.
        if len(payload) > 131072 or endpoint.send(payload) != len(payload):
            raise RuntimeError('Telemetry IPC capacity exceeded')
        exported = j.spec
        previous, last_sent = key, now

    receiver.notify = notify
    receiver.run()


class ProcessReceiver(Receiver):
    """The parent holds only small read-only evidence and applies runtime gates."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.process = None

    def status(self, runtime=None):
        result = super().status(runtime)
        if self.process is None or not self.process.is_alive():
            result.update(record_ready=False, native_consumption_fresh=False,
                          reason='native_telemetry_worker_unavailable')
        return result

    def run(self):
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent.settimeout(.05)
        process = multiprocessing.get_context('spawn').Process(
            target=_worker, args=(child, self.ports, self.export_spec), daemon=False)
        self.process = process
        try:
            process.start()
            child.close()
            while not self.stop.is_set():
                try:
                    payload = parent.recv(131073)
                except socket.timeout:
                    if not process.is_alive():
                        raise RuntimeError('Telemetry worker exited')
                    continue
                if not payload or len(payload) > 131072:
                    raise RuntimeError('Telemetry IPC closed or oversized')
                value = json.loads(payload)
                with self.lock:
                    self.error = value.get('receiver_error')
                    self.diagnostics = dict(value.get('diagnostics',{}), ipc_sent_ns=value.get('ipc_sent_ns'), ipc_received_ns=time.monotonic_ns())
                    j = self.join
                    if 'spec' in value:
                        j.spec = value['spec']
                    j.sessions = value['sessions']
                    j.generation = value['generation']
                    j.writer_generation = value['writer_generation']
                    j.failed = value['failed']
                    last = value['last']
                    # Heartbeats must neither refresh timestamps nor duplicate progress.
                    if last and (not j.last or (last['session_id'],last['index']) !=
                                 (j.last['session_id'],j.last['index'])):
                        if (last.get('replay') or {}).get('scheduled_frame') is True:
                            if len(self.replay_evidence) == self.replay_evidence.maxlen:
                                raise RuntimeError('Replay evidence consumer fell behind')
                            self.replay_evidence.append(last)
                    j.last = last
        except Exception as exc:
            with self.lock:
                self.error = self.error or type(exc).__name__ + ': ' + str(exc)
                self.join.last = None
        finally:
            parent.close(); child.close()
            if process.pid is not None:
                process.join(timeout=1)
                if process.is_alive():
                    process.terminate(); process.join(timeout=1)
                if process.is_alive():
                    process.kill(); process.join(timeout=1)
