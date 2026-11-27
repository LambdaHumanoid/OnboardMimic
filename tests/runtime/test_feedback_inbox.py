"""Loopback sockets and fake lifecycle only; no robot services or commands."""
import threading
import time
import unittest
from onboardmimic.backends.sonic.feedback import FeedbackInbox
from onboardmimic.backends.sonic.transport import Transport
from tests.fixtures import NativeLifecycle


class FeedbackInboxTests(unittest.TestCase):
    def test_decode_delay_is_not_misreported_as_queue_residence(self):
        now=[1.045]
        inbox=FeedbackInbox(('rt/lowstate',),clock=lambda:now[0])
        inbox.put('rt/lowstate',{'seq':1},1.,decoded_at=1.045,decode_cpu_ns=200000)
        now[0]=1.046
        self.assertEqual(inbox.drain(),[('rt/lowstate',1.,{'seq':1})])
        inbox.mark_applied('rt/lowstate',1.,1.047)
        row=inbox.snapshot()['applied']['rt/lowstate']
        self.assertAlmostEqual((row['decoded_at']-row['received_at'])*1000,45)
        self.assertAlmostEqual(row['queue_wait_ms'],1)
        self.assertAlmostEqual(row['drain_to_apply_ms'],1)
        self.assertEqual(row['decode_cpu_ns'],200000)
        self.assertEqual(row['received_at'],1.)  # No freshness timestamp rewriting.

    def test_new_arrival_cannot_relabel_applied_packet_timing(self):
        now=[1.]
        inbox=FeedbackInbox(('rt/lowstate',),clock=lambda:now[0])
        inbox.put('rt/lowstate',{'seq':1},1.)
        now[0]=1.01;inbox.drain()
        now[0]=1.02;inbox.put('rt/lowstate',{'seq':2},1.02)
        inbox.mark_applied('rt/lowstate',1.,1.021)
        status=inbox.snapshot()
        self.assertEqual(status['applied']['rt/lowstate']['received_at'],1.)
        self.assertEqual(status['topics']['rt/lowstate']['received_at'],1.02)
        inbox.mark_applied('rt/lowstate',1.02,1.022)
        self.assertEqual(inbox.snapshot()['applied']['rt/lowstate']['received_at'],1.)

    def test_bounded_latest_per_topic_and_original_timestamps(self):
        inbox=FeedbackInbox(('rt/lowstate','rt/secondary_imu'))
        for i in range(10000):inbox.put('rt/lowstate',{'seq':i},float(i))
        inbox.put('unexpected',{},10001.)
        inbox.put('rt/secondary_imu',{},9998.5)
        inbox.put('rt/lowstate',{'seq':-1},1.)
        self.assertEqual(len(inbox.pending),2)
        self.assertEqual(inbox.snapshot()['replaced'],9999)
        self.assertEqual(inbox.drain(),[('rt/secondary_imu',9998.5,{}),('rt/lowstate',9999.,{'seq':9999})])
        self.assertEqual(inbox.drain(),[])

    def test_socket_reception_continues_while_control_lock_is_held(self):
        import msgpack,zmq
        ctx=zmq.Context();pub=ctx.socket(zmq.PUB)
        port=pub.bind_to_random_port('tcp://127.0.0.1')
        g=NativeLifecycle([0.]*29,lambda:None,lambda:'',lambda:None)
        g.feedback_inbox=FeedbackInbox(g.robot.state_topics)
        transport=Transport(g,port)
        worker=threading.Thread(target=transport.receive_feedback,args=(ctx,))
        worker.start()
        try:
            self.assertTrue(transport.feedback_ready.wait(1))
            with g.lock:
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    pub.send_multipart([b'rt/lowstate',msgpack.packb({'seq':1})])
                    if g.feedback_inbox.snapshot()['topics']:break
                    time.sleep(.005)
                self.assertIn('rt/lowstate',g.feedback_inbox.snapshot()['topics'])
                first=g.feedback_inbox.snapshot()['topics']['rt/lowstate']['received_at']
                until=time.monotonic()+.08
                while time.monotonic()<until:
                    pub.send_multipart([b'rt/lowstate',msgpack.packb({'seq':2})])
                    time.sleep(.002)
                last=g.feedback_inbox.snapshot()['topics']['rt/lowstate']['received_at']
                self.assertGreater(last-first,.05)
                self.assertNotIn('rt/lowstate',g.latest)
                g.refresh_feedback()
                self.assertGreaterEqual(g.latest['rt/lowstate'][0],last)
                self.assertEqual(g.latest['rt/lowstate'][1]['seq'],2)
            transport.stop.set();worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertIsNone(transport.error)
        finally:
            transport.stop.set();worker.join(2);pub.close(0);ctx.term()
