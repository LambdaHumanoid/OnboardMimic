"""Offline attribution of slow computation, GC and waiting; no robot endpoints."""
import gc
import tempfile
import json
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from onboardmimic.backends.sonic.native.join_profile import JoinProfile
from onboardmimic.backends.sonic.native.spikes import SpikeRecorder
from onboardmimic.backends.sonic.native import consumption
from tests.sonic import test_native_consumption as native_tests


class JoinProfileTests(TestCase):
    def test_gc_and_stages_retained_without_changing_consumed_sample(self):
        case=native_tests.NativeConsumptionTests();case.setUp();expected=case.consume()
        case.setUp();events=[];before=list(gc.callbacks)
        profile=JoinProfile(lambda stage,**v:events.append(dict(stage=stage,**v)))
        profile.THRESHOLD_NS=0;case.join.profile=profile
        original=consumption.vector;collected=[]
        def vector(*args):
            if not collected:gc.collect(2);collected.append(True)
            return original(*args)
        try:
            profile.begin()
            with patch.object(consumption,'vector',side_effect=vector):actual=case.consume()
            profile.finish('bridge',case.commit)
            self.assertEqual(actual,expected)
            row=events[-1]
            self.assertEqual(row['stage'],'slow_ingest')
            self.assertTrue(any(g['generation']==2 for g in row['gc_events']))
            self.assertEqual([p['stage'] for p in row['stages']],
                ['lookup_and_identity','decoder_validation','history_validation_and_assembly',
                 'output_validation','encoder_validation','contract_and_session_hash','sample_assembly_and_continuity','remaining_ingest'])
            self.assertGreaterEqual(row['cpu_ns'],0)
            self.assertNotIn('input',row)
            row['at_ns']=time.monotonic_ns()
            with tempfile.TemporaryDirectory() as directory:
                recorder=SpikeRecorder(directory);recorder.observe(row,json.dumps(row)+'\n');recorder.finish(True)
                saved=json.loads(next((Path(directory)/'timing-incidents').glob('*/snapshot.json')).read_text())
                self.assertEqual(saved['triggers'][0]['kind'],'slow_ingest')
                self.assertTrue(saved['triggers'][0]['event']['gc_events'])
        finally:profile.close()
        self.assertEqual(gc.callbacks,before)

    def test_fast_path_does_not_emit_and_close_is_idempotent(self):
        rows=[];profile=JoinProfile(lambda *args,**kw:rows.append(kw))
        try:
            profile.THRESHOLD_NS=1_000_000_000
            profile.begin();profile.mark('test');profile.finish('native',{'kind':1,'index':2})
            self.assertEqual(rows,[])
            for _ in range(40):profile._gc('start',{'generation':0});profile._gc('stop',{})
            self.assertEqual(len(profile.collections),32)
        finally:profile.close();profile.close()
