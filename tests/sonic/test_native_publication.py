"""Loopback-only publication: ordering, backpressure and child failure."""
import json
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from onboardmimic.backends.sonic.native.publication import Publication, CAPACITY
from onboardmimic.backends.sonic.native.consumption import TOPIC


def slow_worker(*args):
    from onboardmimic.backends.sonic.native.publication import _publish
    original = json.dumps
    def encode(value, **kwargs):
        if value.get('index') == 1: time.sleep(.1)
        return original(value, **kwargs)
    with patch('onboardmimic.backends.sonic.native.publication.json.dumps', side_effect=encode):
        _publish(*args)


def stalled_worker(items, replies, port, export_spec, parent_pid):
    replies.send(dict(kind='ready'))
    time.sleep(10)


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]


def sample(index):
    return dict(index=index, timestamp_monotonic_ns=123456,
        reference_evidence='consumed', output_commit={'sequence':index},
        capture_diagnostics={'publication_sequence':index}, decoder_input=[.25]*994)


class PublicationTests(unittest.TestCase):
    def test_slow_serialization_is_fifo_and_does_not_rewrite_evidence(self):
        import zmq
        ctx=zmq.Context();sub=ctx.socket(zmq.SUB);sub.setsockopt(zmq.SUBSCRIBE,TOPIC)
        p=port();sub.connect('tcp://127.0.0.1:'+str(p))
        with patch('onboardmimic.backends.sonic.native.publication._publish',slow_worker):
            publisher=Publication(p)
        try:
            time.sleep(.2)  # Allow the loopback SUB handshake, no motion services.
            originals=[sample(i) for i in range(1,4)]
            began=time.monotonic()
            for s in originals:publisher.submit('sample',s)
            self.assertLess(time.monotonic()-began,.08)  # Worker sleeps 100 ms.
            received=[];acks=[];deadline=time.monotonic()+2
            while (len(received)<3 or len(acks)<3) and time.monotonic()<deadline:
                if sub.poll(5):received.append(json.loads(sub.recv_multipart()[1]))
                acks.extend(publisher.check())
            self.assertEqual([x['index'] for x in received],[1,2,3])
            self.assertEqual([x['index'] for x in acks],[1,2,3])
            for s,actual in zip(originals,received):
                self.assertNotIn('publisher',s['capture_diagnostics'])
                actual['capture_diagnostics'].pop('publisher')
                self.assertEqual(s,actual)
            self.assertGreater(acks[1]['serialize_started_ns']-acks[1]['enqueued_ns'],80_000_000)
        finally:
            publisher.close();sub.close(0);ctx.term()
        self.assertFalse(publisher.process.is_alive())

    def test_capacity_failure_is_explicit_and_sticky(self):
        with patch('onboardmimic.backends.sonic.native.publication._publish',stalled_worker):
            publisher=Publication(port())
        try:
            for i in range(CAPACITY):publisher.submit('sample',sample(i))
            with self.assertRaisesRegex(RuntimeError,'queue full'):publisher.submit('sample',sample(99))
            self.assertEqual(len(publisher.pending),CAPACITY)
            with self.assertRaisesRegex(RuntimeError,'queue full'):publisher.check()
        finally:publisher.close()

    def test_stalled_ack_and_dead_child_fail_closed(self):
        with patch('onboardmimic.backends.sonic.native.publication._publish',stalled_worker):
            publisher=Publication(port())
        try:
            publisher.submit('sample',sample(1))
            at=publisher.pending[0][1]
            with patch('onboardmimic.backends.sonic.native.publication.time.monotonic_ns',return_value=at+250_000_001):
                with self.assertRaisesRegex(RuntimeError,'acknowledgement expired'):publisher.check()
        finally:publisher.close()
        publisher=Publication(port())
        try:
            publisher.process.kill();publisher.process.join(1)
            with self.assertRaisesRegex(RuntimeError,'publisher exited'):publisher.check()
        finally:publisher.close()

    def test_spec_export_and_encoding_failure_are_acknowledged(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'spec.json';publisher=Publication(port(),path)
            try:
                publisher.submit('spec',{'schema':'offline-only'})
                deadline=time.monotonic()+2
                while publisher.pending and time.monotonic()<deadline:
                    publisher.check();time.sleep(.005)
                self.assertFalse(publisher.pending)
                self.assertEqual(json.loads(path.read_text()),{'schema':'offline-only'})
                bad=sample(1);bad['decoder_input']=[float('nan')]
                publisher.submit('sample',bad)
                with self.assertRaises(RuntimeError):
                    while time.monotonic()<deadline:publisher.check();time.sleep(.005)
            finally:publisher.close()
