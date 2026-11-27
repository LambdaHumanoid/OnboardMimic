"""Bounded FIFO publication in a separate process, away from the receiver GIL.

Only validated, exclusively owned samples enter this queue. No retries, frame
replacement or timestamp rewriting. Failure is reported to the receiver.
"""
from collections import deque
import json
import multiprocessing
import os
from pathlib import Path
import queue
import time

from .consumption import TOPIC

# At most 16 compact acknowledgements can be outstanding; their pipe payload
# stays bounded. Replies never contain tensors.
CAPACITY = 16
# Same upper age bound as native evidence ingestion; never an extension to its
# 60 ms live recording check. This bounds a stalled publisher independently.
MAX_PENDING_NS = 250_000_000


def _publish(items, replies, port, export_spec, parent_pid):
    import zmq
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.SNDHWM, 16)
    try:
        socket.bind('tcp://127.0.0.1:'+str(port))
        replies.send(dict(kind='ready'))
        while os.getppid() == parent_pid:
            try: item = items.get(timeout=.05)
            except queue.Empty: continue
            if item is None: break
            sequence, enqueued_ns, kind, value = item
            started = time.monotonic_ns(); cpu = time.thread_time_ns()
            if started-enqueued_ns > MAX_PENDING_NS:
                raise RuntimeError('Telemetry publication queue expired')
            if kind == 'spec':
                if export_spec:
                    path = Path(export_spec)
                    tmp = path.with_name(path.name+'.tmp')
                    tmp.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
                    tmp.replace(path)
                reply = dict(kind='spec', sequence=sequence)
            elif kind == 'sample':
                # This process owns the deserialized object, so adding local
                # publication timing cannot mutate the receiver's evidence.
                value['capture_diagnostics']['publisher'] = dict(
                    enqueued_ns=enqueued_ns, serialize_started_ns=started,
                    queue_delay_ns=started-enqueued_ns)
                payload = json.dumps(value, allow_nan=False, separators=(',', ':')).encode()
                serialized = time.monotonic_ns()
                if len(payload) > 1048576: raise ValueError('Telemetry sample too large')
                socket.send_multipart([TOPIC, payload], flags=zmq.NOBLOCK)
                reply = dict(kind='sample', sequence=sequence, index=value['index'],
                    publication_sequence=value['capture_diagnostics']['publication_sequence'],
                    enqueued_ns=enqueued_ns, serialize_started_ns=started,
                    serialized_ns=serialized, sent_ns=time.monotonic_ns(),
                    thread_cpu_ns=time.thread_time_ns()-cpu)
            else: raise ValueError('Unknown publication item')
            replies.send(reply)
    except Exception as exc:
        try: replies.send(dict(kind='error', error=type(exc).__name__+': '+str(exc)))
        except (OSError, EOFError): pass
    finally:
        socket.close(0); context.term()
        replies.close()


class Publication:
    def __init__(self, port, export_spec=None):
        ctx = multiprocessing.get_context('spawn')
        self.items = ctx.Queue(maxsize=CAPACITY)
        self.replies, reply_sender = ctx.Pipe(duplex=False)
        self.process = ctx.Process(target=_publish,
            args=(self.items,reply_sender,port,export_spec,os.getpid()), daemon=True)
        self.pending = deque(); self.sequence = 0; self.latest = None
        self.error = None; self.closed = False
        try:
            self.process.start()
            reply_sender.close()
            if not self.replies.poll(5): raise RuntimeError('Telemetry publisher startup timed out')
            reply = self.replies.recv()
            if reply.get('kind') != 'ready': raise RuntimeError(reply.get('error','Publisher not ready'))
        except Exception:
            reply_sender.close(); self.close(); raise

    def check(self):
        if self.error: raise RuntimeError(self.error)
        try:
            completed = []
            while self.replies.poll():
                try: reply = self.replies.recv()
                except EOFError: raise RuntimeError('Telemetry publisher exited') from None
                if reply.get('kind') == 'error': raise RuntimeError(reply['error'])
                if not self.pending or reply.get('sequence') != self.pending[0][0]:
                    raise RuntimeError('Telemetry publication acknowledgement out of order')
                self.pending.popleft()
                if reply['kind'] == 'sample':
                    self.latest = reply; completed.append(reply)
            if not self.process.is_alive(): raise RuntimeError('Telemetry publisher exited')
            if self.pending and time.monotonic_ns()-self.pending[0][1] > MAX_PENDING_NS:
                raise RuntimeError('Telemetry publication acknowledgement expired')
            return completed
        except Exception as exc:
            self.error = str(exc); raise

    def submit(self, kind, value):
        if self.closed or self.error: raise RuntimeError(self.error or 'Telemetry publisher closed')
        # Count unacknowledged items, not just the queue: include the worker and
        # feeder thread so memory and outstanding work stay bounded together.
        if len(self.pending) >= CAPACITY:
            self.error = 'Telemetry publication queue full'
            raise RuntimeError(self.error)
        self.sequence += 1; now = time.monotonic_ns()
        try: self.items.put_nowait((self.sequence,now,kind,value))
        except queue.Full:
            self.error = 'Telemetry publication queue full'
            raise RuntimeError(self.error) from None
        self.pending.append((self.sequence,now))

    def close(self):
        if self.closed: return
        self.closed = True
        try: self.items.put_nowait(None)
        except queue.Full: pass
        if self.process.pid is not None:
            self.process.join(timeout=.5)
            if self.process.is_alive(): self.process.terminate(); self.process.join(timeout=.5)
            if self.process.is_alive(): self.process.kill(); self.process.join(timeout=.5)
        # A failed worker must not leave shutdown blocked on a full pipe.
        self.items.cancel_join_thread(); self.items.close()
        self.replies.close()
