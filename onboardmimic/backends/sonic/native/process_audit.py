"""Bounded, best-effort audit writer isolated from execution evidence and its GIL."""
import json
from collections import deque
import multiprocessing
import os
from pathlib import Path
import queue
import socket
from types import SimpleNamespace
import time

from .audit import Audit


class _AuditRows:
    """Unpack IPC batches while preserving the row-based capacity and FIFO."""
    def __init__(self, items, slots, capacity):
        self.items, self.slots, self.capacity = items, slots, capacity
        self.pending = deque()

    def get(self, timeout=None):
        if not self.pending:
            batch = self.items.get(timeout=timeout)
            if batch is None:return None
            self.pending.extend(batch)
        row = self.pending.popleft()
        self.slots.release()
        return row

    def empty(self):
        return not self.pending and self.items.empty()

    def qsize(self):
        return self.capacity-self.slots.get_value()


def _write(items, endpoint, path, max_bytes, owner):
    endpoint.setblocking(False)
    previous = None
    def status(audit):
        nonlocal previous
        value = dict(error=audit.error,latest_incident=audit.latest_incident,
                     timing_incident=audit.spikes.latest)
        if value == previous:return
        try:
            payload=json.dumps(value,separators=(',',':')).encode()
            if len(payload)>131072:raise ValueError('Audit status oversized')
            endpoint.send(payload)
            previous=value
        except BlockingIOError:pass  # Retry the latest status, never block on the reader.
        except (OSError,ValueError):audit.stop.set()
    try:
        audit=Audit(path,max_bytes=max_bytes,start_thread=False,status_callback=status,
                    owner_pid=owner,receiver_pid=owner)
        audit.queue=_AuditRows(*items)
        audit._run()
    finally:endpoint.close()


class ProcessAudit:
    """Same audit fields as Audit, without disk/JSON/GC work on receiver threads.

    Record FIFO and capacity are retained. Only audit status crosses the return
    socket; it cannot provide or refresh any consumption/authority evidence.
    """
    BATCH_ROWS = 16
    BATCH_SECONDS = .004

    def __init__(self,path,capacity=4096,max_bytes=8*1024*1024):
        self.path=Path(path);self.dropped=0;self.error=''
        self.latest_incident=None;self.spikes=SimpleNamespace(latest=None)
        self.closed=False;self.checked=0.
        ctx=multiprocessing.get_context('spawn')
        self.queue=ctx.Queue(maxsize=capacity)
        self.slots=ctx.BoundedSemaphore(capacity)
        self.pending=[];self.pending_since=0.
        self.batch_rows=min(capacity,self.BATCH_ROWS)
        self.endpoint,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        self.endpoint.setblocking(False)
        self.process=ctx.Process(target=_write,args=((self.queue,self.slots,capacity),child,str(path),max_bytes,os.getpid()),daemon=True)
        try:self.process.start()
        except BaseException:
            child.close();self.close();raise
        child.close()

    def check(self,force=False):
        now=time.monotonic()
        # Called on each receive batch, including idle poll cycles. No new timer
        # thread; normal audit records wait at most one poll plus this interval.
        if self.pending and (force or now-self.pending_since >= self.BATCH_SECONDS):
            self.flush()
        if not force and now-self.checked < .1:return
        self.checked=now
        try:
            while True:
                try:payload=self.endpoint.recv(131073)
                except BlockingIOError:break
                if not payload:
                    if not self.closed:self.error=self.error or 'Audit worker exited'
                    break
                if len(payload)>131072:raise ValueError('Audit status oversized')
                value=json.loads(payload)
                self.error=self.error or value['error']
                self.latest_incident=value['latest_incident']
                self.spikes.latest=value['timing_incident']
            if not self.closed and not self.process.is_alive():self.error=self.error or 'Audit worker exited'
        except (OSError,ValueError,KeyError) as exc:self.error=type(exc).__name__+': '+str(exc)

    def emit(self,stage,**fields):
        if self.error or self.closed:return
        row=dict(stage=stage,at_ns=time.monotonic_ns(),**fields)
        row['audit_drops']=self.dropped
        if not self.slots.acquire(block=False):
            self.dropped+=1
            return
        if not self.pending:self.pending_since=time.monotonic()
        self.pending.append(row)
        if (len(self.pending)>=self.batch_rows or
                time.monotonic()-self.pending_since>=self.BATCH_SECONDS or
                stage in ('join_gap','join_rejected','consumption_continuity','invalid_event',
                          'inference_transition','slow_ingest','receiver_loop') or
                (stage=='rejected' and fields.get('reason')!='already_joined_or_superseded')):
            self.flush()

    def flush(self):
        if not self.pending:return
        batch,self.pending=self.pending,[]
        try:self.queue.put_nowait(batch)
        except queue.Full:
            self.dropped+=len(batch)
            for _ in batch:self.slots.release()

    def close(self):
        if self.closed:return
        self.flush()
        self.closed=True
        if self.process.pid is not None:
            try:self.queue.put(None,timeout=.1)
            except queue.Full:pass
            self.process.join(timeout=1)
            if self.process.is_alive():
                self.error=self.error or 'Audit worker shutdown timeout'
                self.process.terminate();self.process.join(timeout=1)
            if self.process.is_alive():self.process.kill();self.process.join(timeout=1)
            self.check(force=True)
        self.endpoint.close()
        self.queue.cancel_join_thread();self.queue.close()
