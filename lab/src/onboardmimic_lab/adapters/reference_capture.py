"""Read-only standard reference telemetry + GoPro ingress. No native WBC dependency."""
import json
import shutil
import socket
import time
from ..contracts import validate_spec, fingerprint
from ..reference_dataset import RawEpisode
from ..video_timing import MpegTsPtsExtractor

TOPIC = b'oml/controller/v1'


def ready(status, spec):
    evidence = status.get('lab', {})
    native_consumption = (spec.get('schema_version') == 2 and spec.get('backend_id') == 'sonic'
                          and spec.get('recording_policy') == 'actual_decoder_consumption')
    mode_allowed = native_consumption or (not status.get('mode_switch_pending')
                                          and status.get('input_mode') in spec['record_modes'])
    return (status.get('state') == 'active' and mode_allowed
            and evidence.get('record_ready') is True
            and evidence.get('contract_sha256') == fingerprint(spec)
            and isinstance(evidence.get('session_id'), str) and bool(evidence['session_id']))


def recording_status(read_status, spec, session, observe):
    """Confirm a transient stale snapshot before aborting; never accept it as ready.

    No frames are consumed by the caller during this bounded recheck. Contract,
    session, mode and every runtime gate must pass on the returned snapshot.
    """
    deadline = time.monotonic() + .1
    expected = fingerprint(spec)
    for attempt in range(5):
        status = read_status()
        evidence = status.get('lab', {})
        observe(status)
        if evidence.get('contract_sha256') != expected:
            raise ValueError('runtime recording contract changed')
        if evidence.get('session_id') != session:
            raise ValueError('runtime recording session changed')
        if attempt and time.monotonic() > deadline:
            break
        if ready(status, spec):
            return status
        validation = evidence.get('recording_validation') or {}
        snapshot_only = (status.get('state') == 'active' and
            evidence.get('reason') == 'recording_status_snapshot_expired' and
            validation.get('failures') == ['recording_status_snapshot_expired'] and
            validation.get('native_record_ready') is True and not validation.get('native_reason'))
        if attempt == 0 and snapshot_only:
            # One new query after an expired response; accept only a fully ready
            # response returned within 100 ms. Never renew this budget again.
            deadline = time.monotonic() + .1
            continue
        transient = (status.get('state') == 'active'
                     and evidence.get('reason') == 'native_consumption_stale_or_warming')
        if not transient or attempt == 4 or time.monotonic() + .02 > deadline:
            break
        time.sleep(.02)
    reason = evidence.get('reason') or 'runtime_not_ready'
    raise ValueError('runtime recording readiness lost: ' + reason)


class ReferenceCapture:
    id = 'reference-v1'
    raw_episode = RawEpisode

    def metadata(self, config, status):
        from .capture import load_spec
        spec = load_spec(config)
        if not ready(status, spec): raise ValueError('runtime has not confirmed recording readiness for this contract')
        if spec.get('schema_version')==2 and spec.get('backend_id')=='sonic':
            from importlib.util import find_spec
            if any(find_spec(name) is None for name in ('av','pyarrow','numpy')):
                raise ValueError('LeRobot export dependencies are not installed')
        return dict(controller_spec=spec, source=('sonic_decoder' if spec.get('recording_policy') == 'actual_decoder_consumption' else status['input_mode']), session_id=status['lab']['session_id'])

    # Called with the lifecycle owner as first argument, like SonicCapture.record.
    @staticmethod
    def record(owner, raw):
        import zmq
        context = zmq.Context(); sub = context.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0); sub.setsockopt(zmq.RCVHWM, 1000)
        topic = TOPIC if raw.spec['schema_version'] == 1 else b'oml/controller/v2-draft'
        sub.setsockopt(zmq.MAXMSGSIZE, 1024*1024); sub.setsockopt(zmq.SUBSCRIBE, topic)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        camera = getattr(owner, 'camera', None)
        parser = None if camera else MpegTsPtsExtractor(); seen = {}; sequence = 0
        begin = time.monotonic(); started = None; checked = 0
        try:
            sub.connect(owner.config['telemetry_endpoint'])
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4*1024*1024)
            poller = zmq.Poller(); poller.register(sub, zmq.POLLIN)
            if camera:camera.begin()
            else:
                udp.bind(('127.0.0.1', owner.config['camera_port'])); udp.setblocking(False)
                poller.register(udp.fileno(), zmq.POLLIN)
            while not owner.stop_event.is_set():
                now = time.monotonic()
                if now-checked >= .5:
                    def observe(status):
                        evidence = status.get('lab', {})
                        raw.append('runtime.jsonl', dict(received_monotonic_ns=time.monotonic_ns(),
                            state=status.get('state'), input_mode=status.get('input_mode'),
                            mode_switch_pending=bool(status.get('mode_switch_pending')),
                            contract_sha256=evidence.get('contract_sha256'), session_id=evidence.get('session_id'),
                            record_ready=evidence.get('record_ready') is True, reason=evidence.get('reason', ''),
                            diagnostics=evidence.get('diagnostics'),
                            recording_validation=evidence.get('recording_validation')))
                    recording_status(owner.runtime_status, raw.spec, raw.manifest['session_id'], observe)
                    if shutil.disk_usage(owner.root).free < 512*1024**2: raise ValueError('disk space below 512 MiB')
                    checked = now
                events = dict(poller.poll(10))
                if sub in events:
                    for _ in range(32):
                        try: packet = sub.recv_multipart(zmq.NOBLOCK)
                        except zmq.Again: break
                        if len(packet) != 2 or packet[0] != topic: raise ValueError('invalid telemetry envelope')
                        sample = json.loads(packet[1])
                        if sample['session_id'] != raw.manifest['session_id']: raise ValueError('telemetry session mismatch')
                        received=time.monotonic_ns()
                        raw.append('runtime.jsonl',dict(event='controller_received',index=sample.get('index'),
                            received_monotonic_ns=received,publication=sample.get('capture_diagnostics')))
                        try:raw.controller(sample, received, take_ownership=True)
                        except (ValueError,KeyError,TypeError,OSError) as exc:
                            raw.append('runtime.jsonl',dict(event='controller_rejected',index=sample.get('index'),
                                received_monotonic_ns=received,reason=str(exc)))
                            raise
                        if sample['reference_evidence'] == 'consumed': seen['controller'] = time.monotonic()
                packets = camera.drain() if camera else []
                if not camera and udp.fileno() in events:
                    for _ in range(64):
                        try: payload, _ = udp.recvfrom(65535)
                        except BlockingIOError: break
                        mono,wall=time.monotonic_ns(),time.time_ns()
                        packets.append((payload,mono,wall,parser.feed(payload)))
                for payload,mono,wall,pts_values in packets:
                    raw.video(payload)
                    for pts in pts_values:
                        sequence += 1
                        raw.append('camera.jsonl', dict(sequence=sequence, pts_90khz=pts,
                                   monotonic_ns=mono, realtime_ns=wall))
                        seen['camera'] = mono/1e9
                now = time.monotonic()
                fresh = all(now-seen.get(k, -1e20) <= .5 for k in ('controller', 'camera'))
                if started is None:
                    if fresh:
                        started = now
                        with owner.lock:
                            if owner.state != 'stopping': owner.state = 'recording'
                            owner.started = now
                    elif now-begin > 15: raise ValueError('等待数据源超时 / Waiting for sources timed out: '+', '.join(k for k in ('controller','camera') if now-seen.get(k,-1e20)>.5))
                elif not fresh: raise ValueError('数据源失联 / Stream lost freshness: '+', '.join(k for k in ('controller','camera') if now-seen.get(k,-1e20)>.5))
                with owner.lock: owner.counts = dict(raw.manifest['counts'])
            if started is None: raise ValueError('采集尚未开始，缺少数据源 / Capture stopped while waiting for: '+', '.join(k for k in ('controller','camera') if time.monotonic()-seen.get(k,-1e20)>.5))
        finally:
            if camera:camera.end()
            udp.close(); sub.close(); context.term()
