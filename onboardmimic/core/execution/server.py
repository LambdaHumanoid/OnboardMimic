"""Explicit opt-in local software execution server; never used by robot runtime."""
import argparse
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import create
from ..gateway import Gateway, make_server

TOPIC = b'oml/controller/v1'


class ExecutionGateway(Gateway):
    def __init__(self, adapter):
        super().__init__([])
        self.adapter = adapter

    def status(self):
        with self.lock:
            return dict(self.adapter.status(), epoch=self.epoch, ticket=self.ticket(), ticket_ttl_ms=250)

    def capabilities(self):
        return dict(schema='onboardmimic.execution.capabilities', schema_version=1,
                    controller_spec=self.adapter.controller_spec(), hardware=False,
                    reference_queue_capacity=1, reference_plan=False, online_chunks=False,
                    protection_policy='software_hold', telemetry_topic=TOPIC.decode())

    def request(self, route, x):
        with self.lock:
            if route != '/api/execution/v1': raise ValueError('Unsupported execution endpoint')
            self.validate(x)
            action = x.get('action')
            a = self.adapter
            if action == 'reset':
                if a.active: raise ValueError('Stop before reset')
                a.reset()
            elif action == 'start':
                if x.get('session_id') != a.session or x.get('contract_sha256') != a.contract:
                    raise ValueError('Execution identity mismatch')
                a.start(x['client'], x.get('mode'))
            elif action == 'reference':
                a.submit(x['client'], x.get('session_id'), x.get('contract_sha256'),
                         x.get('reference_id'), x.get('reference'))
            elif action == 'stop':
                if x.get('session_id') != a.session: raise ValueError('Session mismatch')
                a.stop()
            else: raise ValueError('Unsupported action; plans and heartbeat-only leases are not enabled')
            return self.status()


class SoftwareService:
    """Publisher owns its socket. Separate watchdog does not depend on HTTP polling.

    No heartbeat-only renewal: a valid reference must arrive within the lease.
    A stalled worker is revoked by the watchdog. Process crash has no physical
    effect because this executor cannot access motors.
    """
    def __init__(self, adapter, endpoint):
        parsed = urlparse(endpoint)
        if parsed.scheme != 'tcp' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path:
            raise ValueError('Telemetry must bind tcp://127.0.0.1:PORT')
        self.adapter, self.endpoint = adapter, endpoint
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.threads = [threading.Thread(target=self._publish, daemon=True),
                        threading.Thread(target=self._watch, daemon=True)]

    def start(self):
        for t in self.threads: t.start()
        if not self.ready.wait(3) or self.error:
            self.close()
            raise RuntimeError('Telemetry publisher failed') from self.error
        return self

    def _watch(self):
        while not self.closed.wait(.01): self.adapter.watchdog()

    def _publish(self):
        import zmq
        context = zmq.Context()
        socket = context.socket(zmq.PUB)
        socket.setsockopt(zmq.SNDHWM, 8)
        socket.setsockopt(zmq.LINGER, 0)
        try:
            socket.bind(self.endpoint)
            self.ready.set()
            while not self.closed.wait(self.adapter.config['period_ns']/1e9):
                sample = self.adapter.step()
                if sample is not None:
                    data = json.dumps(sample, allow_nan=False, separators=(',', ':')).encode()
                    if len(data) > 1048576: raise ValueError('Telemetry too large')
                    socket.send_multipart([TOPIC, data], flags=zmq.NOBLOCK)
        except Exception as exc:
            self.error = exc
            self.adapter.stop('execution_worker_failure')
            self.ready.set()
        finally:
            socket.close()
            context.term()

    def close(self):
        self.closed.set()
        for t in self.threads:
            if t.ident is not None: t.join(timeout=3)
        self.adapter.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--config', help='Software controller parameters JSON')
    parser.add_argument('--export-config', required=True)
    parser.add_argument('--telemetry-endpoint', default='tcp://127.0.0.1:16688')
    parser.add_argument('--port', type=int, default=18931)
    args = parser.parse_args()
    adapter = create('software_joint.v1', config=json.loads(Path(args.config).read_text()) if args.config else None)
    service = SoftwareService(adapter, args.telemetry_endpoint)
    server = make_server(ExecutionGateway(adapter), Path(args.token_file).read_text().strip(), args.port)
    service.start()
    try:
        Path(args.export_config).write_text(json.dumps(dict(capture_adapter='reference-v1',
            root='./data-software-joint', same_host=True, telemetry_endpoint=args.telemetry_endpoint,
            camera_port=18554, controller_spec=adapter.controller_spec()), indent=2)+'\n')
        server.serve_forever()
    finally:
        server.server_close()
        service.close()


if __name__ == '__main__': main()
