import json
import socket
import threading
import time
import unittest
import urllib.request
import urllib.error
from copy import deepcopy

from onboardmimic.core.execution import create
from onboardmimic.core.execution.software import fingerprint
from onboardmimic.core.execution.server import ExecutionGateway, SoftwareService, TOPIC
from onboardmimic.core.gateway import make_server


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000000000
        self.a = create('software_joint.v1', clock_ns=lambda: self.now, realtime_ns=lambda: self.now+100000000000)

    def start(self): self.a.start('producer', 'teleoperation')
    def submit(self, **changes):
        args = dict(owner='producer', session=self.a.session, contract=self.a.contract,
                    reference_id='frame1', reference={'q_target': [1., .5, -.2]})
        args.update(changes)
        self.a.submit(**args)
    def tick(self):
        self.now += 20000000
        return self.a.step()

    def test_actual_consumption(self):
        self.start(); self.submit()
        self.assertFalse(self.a.status()['lab']['record_ready'])
        sample = self.tick()
        self.assertEqual(sample['state']['q'], [[0., 0., 0.]])
        self.assertEqual(sample['control_output']['q_target'], [.02, .02, -.02])
        self.assertTrue(self.a.status()['lab']['record_ready'])
        self.assertEqual(sample['state_history_ns'], [self.now])
        self.submit(); sample2 = self.tick()
        self.assertEqual(sample2['state']['q'][0], sample['control_output']['q_target'])

    def test_session_stop_fence_and_reset(self):
        self.start(); self.submit(); self.tick()
        old, token = self.a.session, self.a.authority
        self.a.stop()
        self.assertTrue(self.a.status()['stop_ack'])
        self.assertFalse(self.a.status()['lab']['record_ready'])
        with self.assertRaises(PermissionError): self.a.fence.submit(token, lambda: None)
        self.a.reset(); self.assertNotEqual(old, self.a.session)
        self.start()
        with self.assertRaises(ValueError): self.submit(session=old)

    def test_ownership_modes_queue_and_semantic_hash(self):
        with self.assertRaises(ValueError): self.a.start('producer', 'planner')
        self.start()
        with self.assertRaises(ValueError): self.a.start('other', 'model')
        with self.assertRaises(ValueError): self.submit(owner='other')
        for change in ('semantic', 'joint_names', 'artifact'):
            spec = self.a.controller_spec()
            if change == 'artifact': spec['artifacts']['loaded_config'] = '0'*64
            elif change == 'semantic': spec['reference']['q_target']['semantic'] = 'joint_velocity'
            else: spec['reference']['q_target']['joint_names'].reverse()
            with self.assertRaises(ValueError): self.submit(contract=fingerprint(spec))
        self.submit()
        with self.assertRaises(ValueError): self.submit()
        with self.assertRaises(ValueError): self.a.reset()

    def test_bad_input_and_physical_rejection(self):
        with self.assertRaises(ValueError): create('software_joint.v1', physical=True)
        with self.assertRaises(ValueError): create('unknown')
        self.start()
        for q in ([float('nan'), 0, 0], [0, 0], [3, 0, 0], [True, 0, 0]):
            with self.assertRaises(ValueError): self.submit(reference={'q_target': q})

    def test_freshness_and_timeout(self):
        self.start(); self.submit(); self.tick()
        self.now += 61000000
        self.assertFalse(self.a.status()['lab']['record_ready'])
        self.now += 200000000
        self.a.watchdog()
        self.assertEqual(self.a.status()['stop_reason'], 'execution_timeout')
        self.assertEqual(self.a.monitor.state, 'latched')
        self.assertIsNone(self.a.step())

    def test_http_auth_and_versioned_routes(self):
        g = ExecutionGateway(self.a)
        server = make_server(g, 't'*32)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def call(path, data=None, auth=True):
            request = urllib.request.Request('http://127.0.0.1:%d%s' % (server.server_port, path),
                data=None if data is None else json.dumps(data).encode(),
                headers={'Authorization': 'Bearer '+'t'*32} if auth else {})
            with urllib.request.urlopen(request) as r: return json.load(r)
        try:
            with self.assertRaises(urllib.error.HTTPError) as e: call('/api/execution/v1/capabilities', auth=False)
            self.assertEqual(e.exception.code, 401)
            caps = call('/api/execution/v1/capabilities')
            self.assertNotIn('ticket', json.dumps(caps))
            status = call('/api/controller')
            command = dict(epoch=status['epoch'], ticket=status['ticket'], client='producer', seq=0,
                           action='start', mode='model', session_id=self.a.session, contract_sha256=self.a.contract)
            self.assertEqual(call('/api/execution/v1', command)['state'], 'active')
            command['seq']=1; command['action']='load'
            with self.assertRaises(urllib.error.HTTPError): call('/api/execution/v1', command)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_worker_fault_revokes_authority(self):
        a = create('software_joint.v1')
        a.start('producer', 'model')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        def fail(): raise RuntimeError('injected controller failure')
        a.step = fail
        service = SoftwareService(a, 'tcp://127.0.0.1:'+str(port)).start()
        try:
            time.sleep(.1)
            self.assertFalse(a.active)
            self.assertEqual(a.reason, 'execution_worker_failure')
            self.assertTrue(a.fence.latched)
        finally: service.close()

    def test_real_zmq_and_independent_watchdog(self):
        import zmq
        a = create('software_joint.v1')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        endpoint = 'tcp://127.0.0.1:'+str(port)
        context = zmq.Context(); sub = context.socket(zmq.SUB)
        sub.setsockopt(zmq.SUBSCRIBE, TOPIC); sub.connect(endpoint)
        service = SoftwareService(a, endpoint).start()
        try:
            a.start('producer', 'teleoperation')
            # PUB/SUB subscription propagation is asynchronous; keep producing
            # real ticks until the subscriber receives one, without assuming
            # a fixed slow-joiner delay or fabricating a consumed sample.
            received = False
            for index in range(30):
                with a.lock:
                    if a.pending is None:
                        a.submit('producer', a.session, a.contract, 'actual-'+str(index), {'q_target':[.1, .2, .3]})
                if sub.poll(50):
                    received = True
                    break
            self.assertTrue(received)
            frames = sub.recv_multipart()
            self.assertEqual(len(frames), 2); self.assertEqual(frames[0], TOPIC)
            self.assertEqual(json.loads(frames[1])['reference_evidence'], 'consumed')
            time.sleep(.3)  # no HTTP status calls or producer heartbeats
            self.assertFalse(a.active)
            self.assertEqual(a.reason, 'execution_timeout')
        finally:
            service.close(); sub.close(0); context.term()


if __name__ == '__main__': unittest.main()
