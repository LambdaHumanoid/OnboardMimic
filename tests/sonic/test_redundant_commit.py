"""Redundant mappings never bypass authority checks or unordered joins."""
from copy import deepcopy
import unittest
from onboardmimic.backends.sonic.native.consumption import NativeJoin
from tests.sonic.test_native_consumption import fixture


class RedundantCommitTests(unittest.TestCase):
    def setUp(self):
        self.native,self.bridge,self.a,self.m,self.c,self.now=fixture()
        self.join=NativeJoin(clock_ns=lambda:self.now,capacity=8)
        self.emit('native',dict(kind='manifest',manifest=self.native))
        self.emit('bridge',dict(kind='manifest',manifest=self.bridge))
        self.emit('native',self.a);self.emit('native',self.m);self.emit('bridge',self.c)
        self.sample=deepcopy(self.join.last)
        self.m=dict(self.m,seq=223);self.c=dict(self.c,seq=223)

    def emit(self,source,event):
        return self.join.ingest(source,dict(event,native_session=source+'-TEST',published_monotonic_ns=self.now))

    def test_both_arrival_orders_remove_only_proven_redundant_commit(self):
        for order in (('native','bridge'),('bridge','native')):
            with self.subTest(order=order):
                for source in order:self.emit(source,self.m if source=='native' else self.c)
                self.assertNotIn((2,(111,223)),self.join.pending)
                self.assertEqual(self.join.last,self.sample)

    def test_unknown_commit_is_retained_until_matching_new_inference(self):
        a=dict(self.a,index=2);m=dict(self.m,index=2)
        self.emit('bridge',self.c)
        self.assertIn((2,(111,223)),self.join.pending)
        self.emit('native',m)
        sample=self.emit('native',a)
        self.assertEqual(sample['index'],2)

    def test_redundant_commit_still_revokes_authority(self):
        self.emit('native',self.m)
        self.emit('bridge',dict(self.c,authority=False))
        self.assertIsNone(self.join.last)
        self.assertTrue(self.join.failed)

    def test_new_writer_generation_invalidates_old_mapping_knowledge(self):
        self.emit('native',self.m)
        self.emit('bridge',dict(self.c,generation=self.c['generation']+1))
        self.assertIsNone(self.join.last)
        self.assertFalse(self.join.consumed_mappings)
        self.assertIn((2,(111,223)),self.join.pending)

    def test_bounded_cache_and_expiry(self):
        for seq in range(223,250):self.emit('native',dict(self.m,seq=seq))
        self.assertEqual(len(self.join.consumed_mappings),8)
        self.now+=200000001
        self.emit('bridge',dict(self.c,seq=249))
        self.assertIn((2,(111,249)),self.join.pending)
