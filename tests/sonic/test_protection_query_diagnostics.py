import errno
import json
import os
import struct
import threading
import unittest
from unittest.mock import Mock, patch
from onboardmimic.core.protection.client import ProtectionClient


class QueryDiagnosticsTest(unittest.TestCase):
    def client(self):
        c=ProtectionClient.__new__(ProtectionClient);c.path='/fixture';c._init_diagnostics()
        c.cached={};c.identity={};c.received=0.;c.error='';return c

    def socket(self):
        s=Mock();s.__enter__=Mock(return_value=s);s.__exit__=Mock(return_value=False)
        s.getsockopt.return_value=struct.pack('3i',123,os.getuid(),0)
        s.recv.return_value=b'{"ok":true,"protocol":1,"query_diagnostics":{"failures":0}}'
        return s

    def test_failure_stage_and_errno_survive_success(self):
        for method,stage in [('connect','connect'),('sendall','send'),('recv','receive')]:
            with self.subTest(stage=stage):
                c=self.client();s=self.socket();getattr(s,method).side_effect=BrokenPipeError(errno.EPIPE,'Broken pipe')
                with patch('onboardmimic.core.protection.client.socket.socket',return_value=s):
                    with self.assertRaises(RuntimeError):c._request('status')
                failure=c.snapshot()['query_diagnostics']['last_failure']
                self.assertEqual((failure['phase'],failure['errno']),(stage,errno.EPIPE))
                s=self.socket()
                with patch('onboardmimic.core.protection.client.socket.socket',return_value=s):c._request('status')
                snapshot=c.snapshot()['query_diagnostics']
                self.assertEqual(snapshot['last_failure'],failure)
                self.assertEqual(snapshot['recent'][-1]['phase'],'complete')
                sent=json.loads(s.sendall.call_args.args[0]);self.assertEqual(sent['diagnostic_id'],snapshot['recent'][-1]['id'])
                self.assertEqual(snapshot['server'],{'failures':0})

    def test_bounded_metadata_does_not_copy_request_payload(self):
        c=self.client();s=self.socket()
        with patch('onboardmimic.core.protection.client.socket.socket',return_value=s):
            for i in range(20):c._request('status',private_fixture='must-not-log')
        d=c.snapshot()['query_diagnostics'];self.assertEqual(len(d['recent']),16)
        self.assertNotIn('must-not-log',json.dumps(d))
        self.assertTrue(all(r['finished_ns']>=r['started_ns'] for r in d['recent']))
