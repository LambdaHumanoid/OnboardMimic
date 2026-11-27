"""Private-process GC scope and identity-cache invalidation, no robot actions."""
import gc
from unittest import TestCase
from unittest.mock import Mock,patch

from onboardmimic.backends.sonic.native import consumption,process_receiver
from onboardmimic.core.execution.software import fingerprint
from tests.sonic import test_native_consumption as fixtures


class IdentityCacheTests(TestCase):
    def test_hashes_are_reused_only_for_same_validated_identity(self):
        case=fixtures.NativeConsumptionTests();case.setUp()
        contract=fingerprint(case.join.spec);spec=case.join.spec
        with patch.object(consumption,'fingerprint',wraps=fingerprint) as hashed:
            for i in range(1,21):
                case.attempt['index']=case.mapping['index']=i
                case.mapping['seq']=case.commit['seq']=i+221
                sample=case.consume()
                self.assertEqual(sample['contract_sha256'],contract)
                case.emit('native',dict(kind='manifest',manifest=case.native))
                case.emit('bridge',dict(kind='manifest',manifest=case.bridge))
                self.assertIs(case.join.spec,spec)
                self.assertIsNotNone(case.join.last)
            self.assertEqual(hashed.call_count,1)  # Initial session only, no per-frame spec serialization.
        self.assertEqual(case.join.status()['contract_sha256'],contract)

    def test_model_generation_and_session_changes_cannot_reuse_old_identity(self):
        case=fixtures.NativeConsumptionTests();case.setUp();old=case.consume()
        case.native['artifacts']['decoder_loaded_trt']='f'*64
        case.emit('native',dict(kind='manifest',manifest=case.native))
        self.assertIsNone(case.join.last)
        new=case.consume()
        self.assertNotEqual(new['contract_sha256'],old['contract_sha256'])
        self.assertEqual(new['contract_sha256'],fingerprint(case.join.spec))
        case.attempt.update(index=2,generation=2);case.mapping.update(index=2,generation=2,seq=223)
        case.commit.update(seq=223,generation=10)
        changed=case.consume()
        self.assertNotEqual(changed['session_id'],new['session_id'])
        case.join.ingest('native',dict(kind='manifest',manifest=case.native,
            native_session='new-process',published_monotonic_ns=case.now))
        self.assertIsNone(case.join.last)
        self.assertIsNone(case.consume())  # Old socket-session evidence still rejected.


class ReceiverGCTests(TestCase):
    def test_only_private_worker_disables_cyclic_gc_and_restores_on_error(self):
        initial=gc.isenabled();observed=[]
        receiver=Mock()
        def run():
            observed.append(gc.isenabled());raise RuntimeError('offline failure')
        receiver.run.side_effect=run
        with patch.object(process_receiver,'Receiver',return_value=receiver) as factory:
            endpoint=Mock()
            with self.assertRaisesRegex(RuntimeError,'offline failure'):
                process_receiver._worker(endpoint,[17689,17690,17691],None)
            self.assertEqual(observed,[False])
            self.assertEqual(gc.isenabled(),initial)
            self.assertTrue(factory.call_args.kwargs['isolate_audit'])
            endpoint.close.assert_called_once()

    def test_existing_disabled_gc_policy_is_preserved(self):
        initial=gc.isenabled()
        try:
            gc.disable()
            with patch.object(process_receiver,'_receive'):
                process_receiver._worker(Mock(),[17689,17690,17691],None)
            self.assertFalse(gc.isenabled())
        finally:
            if initial:gc.enable()
