import unittest
from onboardmimic.backends.sonic.native.continuity import ConsumptionContinuity


class ContinuityTest(unittest.TestCase):
    def setUp(self):
        self.c = ConsumptionContinuity(); self.pub = dict(native=0,bridge=0)
        self.emit('native',kind='manifest'); self.emit('bridge',kind='manifest')
        self.sample(1,10)

    def emit(self, source, **event):
        self.pub[source] += 1
        self.c.observe(source,dict(event,native_session=source,publication_sequence=self.pub[source],queue_drops=0))

    def attempt(self, index, seq):
        self.emit('native',kind=0,index=index,generation=1,inference_ok=True,command_ok=True)
        self.emit('native',kind=1,index=index,generation=1,stream=7,seq=seq)

    def sample(self, index, seq):
        self.attempt(index,seq)
        self.emit('bridge',kind=2,stream=7,seq=seq,authority=True)
        s=dict(index=index,session_id='session',output_commit=dict(stream=7,sequence=seq,generation=1))
        return self.c.annotate(s,1)

    def test_transient_sent_but_uncommitted_attempt_is_proven(self):
        self.attempt(2,11)
        self.emit('bridge',kind=2,stream=7,seq=10,authority=True)
        result=self.sample(3,12)
        self.assertEqual(result['reason'],'superseded_before_commit')
        self.assertEqual((result['sequence'],result['segment'],result['skipped_attempts']),(2,0,1))

    def test_missing_telemetry_never_becomes_nonexecution(self):
        for source in ('native','bridge'):
            with self.subTest(source=source):
                self.setUp();self.attempt(2,11);self.pub[source]+=1
                self.assertEqual(self.sample(3,12)['segment'],1)

    def test_existing_commit_not_hidden_even_if_join_arrived_late(self):
        self.attempt(2,11)
        self.emit('bridge',kind=2,stream=7,seq=11,authority=True)
        self.assertEqual(self.sample(3,12)['reason'],'unresolved')

    def test_missing_attempt_or_send_is_unknown(self):
        self.assertEqual(self.sample(3,12)['reason'],'unresolved')
        self.setUp();self.emit('native',kind=0,index=2,generation=1,inference_ok=True,command_ok=True)
        self.assertEqual(self.sample(3,12)['reason'],'unresolved')

    def test_authority_failure_and_publication_drop_break_proof(self):
        self.attempt(2,11);self.emit('bridge',kind=2,stream=7,seq=10,authority=False)
        self.assertEqual(self.sample(3,12)['segment'],1)
        self.setUp();self.attempt(2,11)
        self.c.observe('native',dict(kind='manifest',native_session='native',publication_sequence=5,queue_drops=1))
        self.assertEqual(self.sample(3,12)['segment'],1)

    def test_adjacent_confirmed_samples_need_no_negative_proof(self):
        self.pub['bridge']+=1
        self.assertEqual(self.sample(2,11)['reason'],'adjacent')

    def test_prior_producer_loss_cannot_be_hidden_by_new_baseline(self):
        self.c.sources['bridge'] = ('bridge',self.pub['bridge'],(1,0))
        self.c.previous = (self.c.previous[0],self.c.epoch)
        self.attempt(2,11)
        self.attempt(3,12)
        self.pub['bridge']+=1
        self.c.observe('bridge',dict(kind=2,native_session='bridge',publication_sequence=self.pub['bridge'],queue_drops=1,stream=7,seq=12,authority=True))
        result=self.c.annotate(dict(index=3,session_id='session',output_commit=dict(stream=7,sequence=12,generation=1)),1)
        self.assertEqual(result['reason'],'unresolved')

    def test_receiver_history_is_bounded(self):
        for i in range(5000):self.attempt(i+2,i+11)
        self.assertEqual(len(self.c.mappings),4096)
        self.assertEqual(len(self.c.attempts),4096)


class GapDiagnosticTest(ContinuityTest):
    def test_missing_send_is_explicit(self):
        self.emit('native',kind=0,index=2,generation=1,inference_ok=True,command_ok=True)
        result=self.sample(3,12)
        self.assertEqual(result['reason'],'unresolved')
        self.assertEqual(result['diagnostic']['attempts'],[dict(index=2,inference=(1,True),sends=[])])

    def test_transport_gap_is_explicit(self):
        self.attempt(2,11);self.pub['native']+=1
        result=self.sample(3,12)
        self.assertFalse(result['diagnostic']['epoch_unchanged'])
        self.assertEqual(result['segment'],1)

class BufferReplacementTest(ContinuityTest):
    def proof(self, selected=False, generation=1):
        self.emit('native',kind=0,index=2,generation=1,inference_ok=True,command_ok=True)
        self.emit('native',kind=0,index=3,generation=1,inference_ok=True,command_ok=True,
            buffer_replacement=dict(boundary='serialized_writer_selection',index=2,generation=generation,selected=selected))

    def test_unselected_replacement_is_not_an_executed_missing_frame(self):
        self.proof()
        result=self.sample(3,12)
        self.assertEqual(result['reason'],'superseded_before_commit')
        self.assertEqual(result['skipped_attempts'],1)
        self.assertEqual(result['segment'],0)

    def test_selected_or_wrong_generation_stays_unknown(self):
        for selected,generation in [(True,1),(False,2)]:
            self.setUp();self.proof(selected,generation)
            self.assertEqual(self.sample(3,12)['reason'],'unresolved')

    def test_transport_loss_invalidates_unselected_proof(self):
        self.proof();self.pub['native']+=1
        self.assertEqual(self.sample(3,12)['reason'],'unresolved')

    def test_contradictory_committed_mapping_is_not_hidden(self):
        self.proof();self.emit('native',kind=1,index=2,generation=1,stream=7,seq=11)
        self.emit('bridge',kind=2,stream=7,seq=11,authority=True)
        self.assertEqual(self.sample(3,12)['reason'],'unresolved')
