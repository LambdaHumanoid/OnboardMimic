"""Read-only preflight of the exact relay used by SONIC. Never releases mode."""
import argparse
import json
import time


def check(port=16660, timeout=5.):
    import msgpack
    import zmq
    context = zmq.Context()
    sub = context.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b'rt/')
    sub.setsockopt(zmq.RCVHWM, 8)
    sub.connect('tcp://127.0.0.1:%d' % port)
    seen, counts, previous_tick, status = {}, {}, None, None
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if not sub.poll(20):
                continue
            topic, payload = sub.recv_multipart()
            now = time.monotonic()
            value = msgpack.unpackb(payload, raw=False)
            if topic == b'rt/continuity':
                if value.get('protocol') == 1 and 0 <= now-value['at'] < .2:
                    status = value
            elif topic in (b'rt/lowstate', b'rt/secondary_imu'):
                if topic == b'rt/lowstate':
                    tick = value.get('tick')
                    if type(tick) is not int:
                        raise RuntimeError('LowState has no tick')
                    if previous_tick is not None and not 0 < (tick-previous_tick) % 2**32 < 2**31:
                        raise RuntimeError('LowState tick did not advance')
                    previous_tick = tick
                counts[topic] = counts.get(topic, 0) + 1
                seen[topic] = now
            if (status and status.get('source') in ('primary', 'standby') and
                    now-status['at'] < .2 and
                    all(now-seen.get(t, 0) < .05 and counts.get(t, 0) >= 10
                        for t in (b'rt/lowstate', b'rt/secondary_imu'))):
                return dict(ready=True, source=status['source'], read_only=True,
                            samples={k.decode(): v for k, v in counts.items()})
        raise RuntimeError('Fresh redundant LowState/IMU feedback not ready')
    finally:
        sub.close(0)
        context.term()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=16660)
    parser.add_argument('--timeout', type=float, default=5.)
    args = parser.parse_args()
    print(json.dumps(check(args.port, args.timeout)))
