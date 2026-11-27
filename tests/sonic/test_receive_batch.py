"""FIFO/backpressure and diagnostic tests; no robot or controller endpoints."""
from collections import deque
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zmq

from onboardmimic.backends.sonic.native.receive_batch import ReceiveBatch
from onboardmimic.backends.sonic.native.receiver_profile import ReceiverProfile
from onboardmimic.backends.sonic.native.spikes import SpikeRecorder
from onboardmimic.backends.sonic.native.consumption import RAW_TOPIC
from onboardmimic.backends.sonic.native.receiver import Receiver
from tests.sonic.test_native_consumption import fixture


class Socket:
    def __init__(self, values): self.queue = deque(values)
    def getsockopt(self, option):
        assert option == zmq.EVENTS
        return zmq.POLLIN if self.queue else 0
    def recv_multipart(self, flags):
        assert flags == zmq.NOBLOCK
        if not self.queue: raise zmq.Again()
        return self.queue.popleft()


class BatchTests(unittest.TestCase):
    def test_burst_drains_unequal_sources_without_dropping_or_reordering(self):
        sockets=[Socket(range(33)),Socket(range(20))]
        batch=ReceiveBatch(sockets,clock_ns=lambda:0)
        rows=list(batch.receive(dict.fromkeys(sockets)))
        self.assertEqual([v for source,v in rows if source==0],list(range(33)))
        self.assertEqual([v for source,v in rows if source==1],list(range(20)))
        self.assertEqual([s for s,_ in rows[:40]],[0,1]*20)
        self.assertEqual(batch.counts,[33,20])
        self.assertIsNone(batch.limit)

    def test_time_budget_preserves_unread_frames_and_rotates_first_source(self):
        now=[0];sockets=[Socket(range(10)),Socket(range(10))]
        batch=ReceiveBatch(sockets,clock_ns=lambda:now[0])
        first=[]
        for row in batch.receive(dict.fromkeys(sockets)):
            first.append(row);now[0]+=3_000_000
        self.assertEqual(first,[(0,0),(1,0)])
        self.assertEqual(batch.limit,'time')
        self.assertEqual([len(s.queue) for s in sockets],[9,9])
        second=list(batch.receive(dict.fromkeys(sockets)))
        self.assertEqual(second[0],(1,1))
        for source in (0,1):
            self.assertEqual([v for s,v in first+second if s==source],list(range(10)))

    def test_event_budget_and_socket_readiness(self):
        sockets=[Socket(range(100)),Socket(range(100))]
        batch=ReceiveBatch(sockets,clock_ns=lambda:0)
        rows=list(batch.receive({sockets[0]:zmq.POLLIN}))
        self.assertEqual(len(rows),64)
        self.assertEqual(batch.limit,'events')
        self.assertEqual(len(sockets[1].queue),100)
        self.assertEqual(list(batch.receive({})),[])
        self.assertIsNone(batch.limit)

    def test_flood_does_not_starve_other_source_across_short_batches(self):
        now=[0];sockets=[Socket(range(20)),Socket(range(20))]
        batch=ReceiveBatch(sockets,clock_ns=lambda:now[0]);rows=[]
        for _ in range(8):
            for row in batch.receive(dict.fromkeys(sockets)):
                rows.append(row);now[0]+=5_000_000
        self.assertEqual([s for s,_ in rows],[0,1]*4)
        self.assertEqual(batch.limit,'time')


class ReceiverBatchTests(unittest.TestCase):
    def test_each_commit_and_intermediate_revocation_notified_inside_batch(self):
        native,bridge,a,m,c,now=fixture()
        a.update(mode=3,replay=dict(run=42,frame=0,scheduled_frame=True))
        def packet(source,event):
            return [RAW_TOPIC,json.dumps(dict(event,native_session=source,
                published_monotonic_ns=now)).encode()]
        class Sub(Socket):
            def setsockopt(self,*args): pass
            def connect(self,*args): pass
            def close(self,*args): pass
        sockets=[Sub([
            packet('native',dict(kind='manifest',manifest=native)),
            packet('native',a),packet('native',m),[b'invalid-envelope'],
            packet('native',dict(a,index=2,replay=dict(run=42,frame=1,scheduled_frame=True))),
            packet('native',dict(m,index=2,seq=223))]),
            Sub([packet('bridge',dict(kind='manifest',manifest=bridge)),
                 packet('bridge',c),packet('bridge',c),packet('bridge',dict(c,seq=223))])]
        class Context:
            def __init__(self): self.sockets=iter(sockets)
            def socket(self,*args): return next(self.sockets)
            def term(self): pass
        class Poller:
            def register(self,*args): pass
            def poll(self,*args): return [(s,zmq.POLLIN) for s in sockets if s.queue]
        published=[]
        class Publisher:
            def __init__(self,*args): pass
            def check(self): return []
            def submit(self,kind,sample): published.append(sample['index'])
            def close(self): pass
        r=Receiver(17689,17690,17691);r.join.clock_ns=lambda:now
        notifications=[]
        def notify():
            value=(r.join.last['index'] if r.join.last else None,r.join.failed)
            if not notifications or notifications[-1]!=value:notifications.append(value)
            if value==(2,False):r.stop.set()
        r.notify=notify
        with patch.dict('os.environ',{'OM_SONIC_AUDIT':'0'}), \
             patch.object(zmq,'Context',Context),patch.object(zmq,'Poller',Poller), \
             patch('onboardmimic.backends.sonic.native.receiver.Publication',Publisher):
            r.run()
        self.assertIsNone(r.error)
        self.assertEqual(notifications,[(None,False),(1,False),(None,True),(None,False),(2,False)])
        self.assertEqual(published,[1,2])
        self.assertEqual([s['replay']['frame'] for s in r.replay_evidence],[0,1])
        self.assertEqual(r.join.last['timestamp_monotonic_ns'],a['at_ns'])


class ReceiverProfileTests(unittest.TestCase):
    def test_waiting_cpu_phases_and_gc_retained_in_incident_without_tensors(self):
        now=[1_000_000_000];cpu=[100_000_000];rows=[]
        def trace(stage,**row): rows.append(dict(stage=stage,at_ns=now[0],**row))
        join=SimpleNamespace(collections=deque([(1_080_000_000,1_090_000_000,8_000_000,2,123)],maxlen=32))
        with patch('time.monotonic_ns',side_effect=lambda:now[0]), patch('time.thread_time_ns',side_effect=lambda:cpu[0]):
            profile=ReceiverProfile(trace,join)
            profile.begin()
            now[0]+=10_000_000;profile.polled()
            now[0]+=20_000_000;cpu[0]+=2_000_000;profile.mark('decode')
            profile.received(0,{'published_monotonic_ns':now[0]-50_000_000},now[0])
            now[0]+=80_000_000;cpu[0]+=5_000_000;profile.mark('ingest')
            summary=profile.finish(SimpleNamespace(limit='time'))
        self.assertEqual(len(rows),1)
        self.assertEqual(summary['cpu_ns'],7_000_000)
        self.assertEqual(summary['max_active_batch_ns'],100_000_000)
        self.assertEqual(summary['max_receive_delay_ns']['native'],50_000_000)
        self.assertEqual(rows[0]['stages']['decode']['cpu_ns'],2_000_000)
        self.assertEqual(rows[0]['gc_events'][0]['generation'],2)
        self.assertNotIn('stages',summary)
        with tempfile.TemporaryDirectory() as directory:
            recorder=SpikeRecorder(directory)
            recorder.observe(rows[0],json.dumps(rows[0])+'\n');recorder.finish(True)
            snapshot=json.loads(next((Path(directory)/'timing-incidents').glob('*/snapshot.json')).read_text())
            self.assertEqual(snapshot['triggers'][0]['kind'],'slow_receive_loop')
            self.assertEqual(snapshot['triggers'][0]['event']['stages'],rows[0]['stages'])

    def test_idle_poll_is_not_slow_processing_and_window_emission_is_bounded(self):
        now=[1_000_000_000];rows=[]
        with patch('time.monotonic_ns',side_effect=lambda:now[0]):
            profile=ReceiverProfile(lambda stage,**row:rows.append(dict(stage=stage,**row)),SimpleNamespace(collections=[]))
            for i in range(10):
                profile.begin();now[0]+=10_000_000;profile.polled()
                profile.finish(SimpleNamespace(limit=None))
                self.assertEqual(len(rows),int(i==9))
        self.assertEqual(rows[0]['max_active_batch_ns'],0)
        self.assertIsNone(SpikeRecorder.trigger(rows[0]))
        self.assertIsNone(SpikeRecorder.trigger(dict(rows[0],max_active_batch_ns=20_000_000)))
        self.assertEqual(SpikeRecorder.trigger(dict(rows[0],max_active_batch_ns=20_000_001))['kind'],'slow_receive_loop')
