import json
from pathlib import Path
import secrets
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen, Request
from onboardmimic.core.gateway import Gateway, make_server

from onboardmimic.core.client import OnboardClient


class Clock:
    value = 100.
    def __call__(self):return self.value


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        baseline = Path(__file__).with_name('baseline_q.json')
        if baseline.exists():
            q = json.loads(baseline.read_text())
        else:
            text = (Path(__file__).resolve().parents[2] / 'onboardmimic/backends/sonic/deploy/src/g1/g1_deploy_onnx_ref/include/policy_parameters.hpp').read_text()
            text = text.split('const std::array<double, 29> default_angles = {')[1].split('};')[0]
            q = [float(line.split('//')[0].strip().rstrip(',')) for line in text.splitlines() if '//' in line]
        self.g = Gateway(q, self.clock)
        self.seq = 0

    def packet(self, **values):
        self.seq += 1
        s = self.g.status()
        return dict(client='test', seq=self.seq, epoch=s['epoch'], ticket=s['ticket'], **values)

    def active(self):
        self.g.request('/api/controller', self.packet(action='prepare_pose'))
        self.clock.value += 3.01
        self.g.request('/api/controller', self.packet(action='start_policy'))
        self.clock.value += 1.01
        self.g.tick()
        self.clock.value += .6

    def test_no_policy_before_prepare(self):
        with self.assertRaises(ValueError):self.g.request('/api/controller', self.packet(action='start_policy'))
        self.assertFalse(self.g.status()['motion_enabled'])

    def test_prepare_does_not_start_policy(self):
        self.g.request('/api/controller', self.packet(action='prepare_pose'))
        self.clock.value += 4
        self.assertEqual(self.g.status()['state'], 'prepared')
        self.assertIsNone(self.g.last_packet)

    def test_lost_network_stops_locally_without_http(self):
        self.active()
        self.g.request('/api/control', self.packet(move='forward', turn='left'))
        self.assertEqual(self.g.last_packet['speed'], .5)
        self.clock.value += .56
        self.g.tick()
        self.assertEqual(self.g.last_packet['speed'], 0)
        self.assertEqual(self.g.last_packet['mode'], 0)

    def test_delayed_packet_cannot_renew_lease(self):
        self.active()
        p = self.packet(move='forward')
        self.clock.value += .251
        with self.assertRaises(ValueError):self.g.request('/api/control', p)
        self.assertEqual(self.g.lease.deadline, 0)

    def test_stop_invalidates_pending_start(self):
        self.active()
        old = self.packet(action='start_policy')
        self.g.request('/api/controller', {'action': 'estop'})
        with self.assertRaises(ValueError):self.g.request('/api/controller', old)
        self.assertEqual(self.g.status()['state'], 'stopped')
        self.assertIsNone(self.g.last_packet)

    def test_replay_and_second_owner_rejected(self):
        self.active()
        p = self.packet(move='forward')
        self.g.request('/api/control', p)
        with self.assertRaises(ValueError):self.g.request('/api/control', p)
        p = self.packet(move='right');p['client'] = 'other'
        with self.assertRaises(ValueError):self.g.request('/api/control', p)
        self.assertEqual(self.g.lease.move, 'forward')


    def test_estop_cancels_pending_prepare_transition(self):
        self.g.request('/api/controller', self.packet(action='prepare_pose'))
        self.g.request('/api/controller', {'action': 'estop'})
        self.clock.value += 10
        self.assertEqual(self.g.status()['state'], 'stopped')

    def test_bad_ticket_and_bool_are_rejected(self):
        self.active()
        for bad in (None, [], {}, 'bad'):
            p = self.packet(move='forward');p['ticket'] = bad
            with self.assertRaises(ValueError):self.g.request('/api/control', p)
        with self.assertRaises(ValueError):
            self.g.request('/api/control', self.packet(stop='false'))
        self.assertEqual(self.g.lease.deadline, 0)

    def test_client_cancels_lifecycle_if_estop_interrupts_status(self):
        client = OnboardClient('http://127.0.0.1:18921', 'x' * 32)
        def interrupted_status():
            client.generation += 1
            return self.g.status()
        with patch.object(client, 'status', side_effect=interrupted_status), patch.object(client, 'call') as send:
            with self.assertRaises(RuntimeError):client.command('prepare_pose')
            send.assert_not_called()

    def test_client_rejects_nonlocal_and_credential_urls(self):
        for url in ('http://example.com:18921', 'http://127.0.0.1:18921@evil.example', 'http://user@127.0.0.1:18921', 'http://127.0.0.1:18921/path'):
            with self.assertRaises(ValueError):OnboardClient(url, 'x' * 32)

    def test_http_adapter_and_authentication(self):
        token = secrets.token_hex(32)
        server = make_server(self.g, token)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        client = OnboardClient('http://127.0.0.1:%d' % server.server_port, token)
        try:
            with self.assertRaises(HTTPError) as e:urlopen(client.url + '/api/controller')
            self.assertEqual(e.exception.code, 401)
            self.assertFalse(client.status()['motion_enabled'])
            client.command('prepare_pose');self.clock.value += 3.01
            client.command('start_policy');self.clock.value += 1.01
            client.control({'client': 'phone', 'seq': 1, 'move': 'forward'})
            self.assertEqual(self.g.lease.move, 'forward')
            client.command('estop')
            self.assertEqual(client.status()['state'], 'stopped')
            self.assertFalse(client.status()['motion_enabled'])
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__ == '__main__':unittest.main()
