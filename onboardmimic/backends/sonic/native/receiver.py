"""Optional read-only telemetry worker. Errors never interrupt motor transport."""
from collections import deque
import json
import os
import threading
import time
import uuid
from pathlib import Path

from .consumption import NativeJoin, RAW_TOPIC
from .publication import Publication


class Receiver:
    def __init__(self, native_port, bridge_port, publish_port, export_spec=None, *, isolate_audit=False):
        self.ports = [int(native_port), int(bridge_port), int(publish_port)]
        if any(not 1024 <= p <= 65535 for p in self.ports) or len(set(self.ports)) != 3:
            raise ValueError('Three distinct loopback ports required')
        self.join = NativeJoin()
        self.replay_evidence = deque(maxlen=64)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.error = None
        self.diagnostics = {}
        self.notify = None
        self.export_spec = export_spec
        self.isolate_audit = isolate_audit
        self.thread = threading.Thread(target=self.run, daemon=True)

    def status(self, runtime=None):
        with self.lock:
            result = self.join.status(runtime)
            # Bounded metadata only; used to retain evidence when readiness drops
            # before a missing inference index can trigger a gap incident.
            now = time.monotonic_ns()
            result['diagnostics'] = dict(checked_ns=now, receiver_error=self.error,
                last_index=self.join.last.get('index') if self.join.last else None,
                last_source_ns=self.join.last.get('timestamp_monotonic_ns') if self.join.last else None,
                stages={k:dict(v) if isinstance(v,dict) else v for k,v in self.diagnostics.items()
                        if k in ('native_0','native_1','bridge_2','join','publication','loop','ipc_sent_ns','ipc_received_ns')})
            if self.error or self.stop.is_set():
                result.update(record_ready=False, native_consumption_fresh=False, reason='native_telemetry_error')
            return result

    def run(self):
        import zmq
        context = zmq.Context()
        from .audit import Audit
        from .process_audit import ProcessAudit
        audit_type = ProcessAudit if self.isolate_audit else Audit
        audit = None if os.environ.get('OM_SONIC_AUDIT')=='0' else audit_type(
            Path(os.environ.get('OM_SONIC_AUDIT_DIR','var/logs'))/('consumption-receiver-'+str(self.ports[2])+'.jsonl'))
        def trace(stage, **fields):
            if audit:audit.emit(stage,**fields)
        self.join.trace=trace
        from .join_profile import JoinProfile
        from .receiver_profile import ReceiverProfile
        from .receive_batch import ReceiveBatch
        profile = JoinProfile(trace) if audit else None
        self.join.profile = profile
        publication_session=uuid.uuid4().hex; publication_sequence=0; previous_sample=None
        previous_attempt = None
        sockets = []; publisher = None
        try:
            publisher = Publication(self.ports[2], self.export_spec)
            for port in self.ports[:2]:
                s = context.socket(zmq.SUB); sockets.append(s)
                s.setsockopt(zmq.LINGER, 0); s.setsockopt(zmq.RCVHWM, 64)
                s.setsockopt(zmq.MAXMSGSIZE, 1048576); s.setsockopt(zmq.SUBSCRIBE, RAW_TOPIC)
                s.connect('tcp://127.0.0.1:'+str(port))
            poller = zmq.Poller()
            for s in sockets[:2]: poller.register(s, zmq.POLLIN)
            batch = ReceiveBatch(sockets[:2])
            loop = ReceiverProfile(trace, profile) if profile else None
            exported = None
            while not self.stop.is_set():
                if loop: loop.begin()
                if self.isolate_audit and audit:audit.check()
                for acknowledgement in publisher.check():
                    fields = {k:v for k,v in acknowledgement.items() if k not in ('kind','sequence')}
                    self.diagnostics['publication'] = dict(fields)
                    trace('publish_attempt',publication_session=publication_session,**fields)
                if self.notify:
                    self.notify()
                if loop: loop.mark('maintenance')
                ready = dict(poller.poll(10))
                if loop: loop.polled()
                for source_index, frames in batch.receive(ready):
                    source = ('native', 'bridge')[source_index]
                    if loop: loop.mark('receive')
                    try:
                        if len(frames) != 2 or frames[0] != RAW_TOPIC: raise ValueError('Invalid native envelope')
                        received_ns = time.monotonic_ns()
                        cpu_start = time.thread_time_ns()
                        event = json.loads(frames[1])
                        decoded_ns = time.monotonic_ns()
                        if loop:
                            loop.mark('decode')
                            loop.received(source_index, event, received_ns)
                        trace('received',source=source,received_ns=received_ns,decoded_ns=decoded_ns,**{k:event[k] for k in (
                            'native_session','kind','index','stream','seq','generation','published_monotonic_ns','at_ns','finished_ns','send_ns',
                            'inference_ok','command_ok','authority','queue_drops','serialization_drops',
                            'audit_drops','audit_failed','publication_sequence','timing','mode','buffer_replacement') if k in event and k!='at_ns'},source_ns=event.get('at_ns'))
                        if event.get('kind') in (0, 1, 2):
                            self.diagnostics[source+'_'+str(event['kind'])] = dict(
                                received_ns=received_ns, decoded_ns=decoded_ns, **{k:event[k] for k in
                                ('index','at_ns','finished_ns','send_ns','stream','seq','generation',
                                 'published_monotonic_ns','publication_sequence','queue_drops',
                                 'serialization_drops','timing','mode','inference_ok','command_ok','authority') if k in event})
                        if source=='native' and event.get('kind')==0:
                            if previous_attempt and event.get('native_session')==previous_attempt.get('native_session'):
                                gap=event['at_ns']-previous_attempt['at_ns']
                                if gap>60000000 or event.get('mode')!=previous_attempt.get('mode'):
                                    trace('inference_transition',previous_index=previous_attempt['index'],index=event['index'],
                                          previous_mode=previous_attempt.get('mode'),mode=event.get('mode'),gap_ns=gap,
                                          timing=event.get('timing'),previous_timing=previous_attempt.get('timing'))
                            previous_attempt={k:event.get(k) for k in ('native_session','index','at_ns','mode','timing')}
                        if loop: loop.mark('metadata_and_audit')
                        with self.lock:
                            # json.loads created an exclusively owned object. After
                            # transfer, publication and replay only read its data.
                            ingest_started_ns = time.monotonic_ns()
                            if profile: profile.begin()
                            try:
                                sample = self.join.ingest(source, event, take_ownership=True)
                            finally:
                                if profile: profile.finish(source,event)
                            ingest_finished_ns = time.monotonic_ns()
                            spec = self.join.spec
                            if sample and (sample.get('replay') or {}).get('scheduled_frame') is True:
                                self.replay_evidence.append(sample)
                        if loop: loop.mark('ingest')
                        if sample:
                            gap_id = None
                            if previous_sample and sample['session_id']==previous_sample['session_id'] and sample['index']!=previous_sample['index']+1:
                                if (sample.get('consumption') or {}).get('reason') != 'superseded_before_commit':gap_id=uuid.uuid4().hex
                                trace('join_gap',diagnostic_id=gap_id,previous_index=previous_sample['index'],index=sample['index'],session=sample['session_id'],consumption=sample.get('consumption'),previous_sample=previous_sample,sample=dict(sample))
                            previous_sample=sample
                            publication_sequence+=1
                            sample['capture_diagnostics']=dict(publication_session=publication_session,
                                publication_sequence=publication_sequence,joined_monotonic_ns=sample['published_monotonic_ns'],
                                audit_drops=audit.dropped if audit else 0,audit_error=audit.error if audit else 'disabled',
                                gap_diagnostic_id=gap_id,native_incident=audit.latest_incident if audit else None,
                                timing_incident=audit.spikes.latest if audit else None)
                            join_timing = dict(received_ns=received_ns, decoded_ns=decoded_ns,
                                ingest_started_ns=ingest_started_ns, ingest_finished_ns=ingest_finished_ns,
                                thread_cpu_ns=time.thread_time_ns()-cpu_start,
                                source_ns=sample['timestamp_monotonic_ns'], index=sample['index'])
                            self.diagnostics['join'] = join_timing
                            sample['capture_diagnostics']['receiver'] = dict(join_timing)
                            trace('joined',**join_timing,session=sample['session_id'],publication_sequence=publication_sequence,
                                  publication_session=publication_session,commit=sample['output_commit'])
                            # Enqueue once; the child owns JSON work and its ZMQ
                            # socket. No motor/progress evidence waits for this.
                            publisher.submit('sample',sample)
                        if self.export_spec and spec and spec != exported:
                            publisher.submit('spec',spec); exported = spec
                        if loop: loop.mark('publication_enqueue')
                    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
                        trace('invalid_event',source=source,error=type(exc).__name__+': '+str(exc))
                        with self.lock:
                            self.join.continuity.epoch += 1
                            self.join.last = None
                            self.join.failed = True
                    if loop: loop.mark('event_finish')
                    # Each joined frame AND each revocation reaches the parent
                    # before draining the next packet. Batching never coalesces
                    # replay progress or delays an authority/fault notification.
                    if self.notify:
                        self.notify()
                    if loop: loop.mark('notify')
                if loop:
                    summary = loop.finish(batch)
                    if summary: self.diagnostics['loop'] = summary
        except Exception as exc:
            with self.lock: self.error = type(exc).__name__+': '+str(exc)
            trace('receiver_failed',error=self.error)
            # Propagate publication/receiver failure to the parent immediately.
            self.join.last = None
            if self.notify:
                try: self.notify()
                except Exception: pass
        finally:
            self.join.profile = None
            if profile: profile.close()
            if publisher: publisher.close()
            for s in sockets: s.close(0)
            context.term()
            if audit:audit.close()

    def close(self):
        self.stop.set()
        if self.thread.ident is not None: self.thread.join(timeout=3)

    @classmethod
    def from_environment(cls):
        if os.environ.get('OM_SONIC_NATIVE_RECEIVER') != '1': return None
        from .process_receiver import ProcessReceiver
        return ProcessReceiver(os.environ.get('OM_SONIC_NATIVE_TELEMETRY_PORT', '16689'),
                   os.environ.get('OM_SONIC_BRIDGE_TELEMETRY_PORT', '16690'),
                   os.environ.get('OM_SONIC_CONSUMED_TELEMETRY_PORT', '16691'),
                   os.environ.get('OM_SONIC_SPEC_EXPORT'))


def main():
    import argparse
    import time
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-port', type=int, default=16689)
    parser.add_argument('--bridge-port', type=int, default=16690)
    parser.add_argument('--publish-port', type=int, default=16691)
    parser.add_argument('--export-spec', required=True)
    args = parser.parse_args()
    receiver = Receiver(args.native_port, args.bridge_port, args.publish_port, args.export_spec)
    receiver.thread.start()
    try:
        while receiver.thread.is_alive(): time.sleep(.2)
        if receiver.error: raise RuntimeError(receiver.error)
    except KeyboardInterrupt: pass
    finally: receiver.close()


if __name__ == '__main__': main()
