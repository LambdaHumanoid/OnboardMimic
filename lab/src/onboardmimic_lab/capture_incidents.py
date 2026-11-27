"""Persistent per-gap evidence, independent of raw-episode cleanup and OM imports."""
from collections import deque
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid


class CaptureIncidents:
    def __init__(self, root, metadata):
        self.root=Path(root)
        self.metadata=metadata
        self.queue=queue.Queue(maxsize=16)
        self.lock=threading.Lock()
        self.total=0
        self.saved=0
        self.failed=0
        self.recent=deque(maxlen=16)
        self.closed=threading.Event()
        self.worker=threading.Thread(target=self._run,name='capture-incidents',daemon=True)
        self.worker.start()

    def submit(self, previous, sample, received_ns, *, reason=None, context=None):
        current=sample or previous
        row=dict(id=uuid.uuid4().hex,state='saving',index=current['index'],reason=reason,
                 kind='capture_error' if reason else 'controller_gap',
                 previous_index=previous['index'],received_monotonic_ns=received_ns,
                 detected_realtime_ns=time.time_ns(),
                 source_gap_ms=(current['timestamp_monotonic_ns']-previous['timestamp_monotonic_ns'])/1e6)
        with self.lock:
            self.total+=1;self.recent.append(row)
        # Sample objects are owned by the recorder and never modified after submission.
        try:self.queue.put_nowait((row,previous,sample,context))
        except queue.Full:
            with self.lock:
                row.update(state='failed',error='diagnostic_queue_full');self.failed+=1

    def snapshot(self):
        with self.lock:return dict(count=self.total,saved=self.saved,failed=self.failed,pending=self.total-self.saved-self.failed,recent=[dict(row) for row in self.recent])

    def _run(self):
        while not self.closed.is_set() or not self.queue.empty():
            try:row,previous,sample,context=self.queue.get(timeout=.05)
            except queue.Empty:continue
            try:
                folder=self.root/row['id'];folder.mkdir(parents=True,exist_ok=False)
                payload=dict(schema=1,episode=self.metadata,event=dict(row),previous_sample=previous,sample=sample,context=context)
                with (folder/'evidence.json').open('w') as f:
                    json.dump(payload,f,allow_nan=False);f.flush();os.fsync(f.fileno())
                fd=os.open(folder,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
                for directory in (folder.parent,folder.parent.parent):
                    fd=os.open(directory,os.O_RDONLY)
                    try:os.fsync(fd)
                    finally:os.close(fd)
                with self.lock:
                    row.update(state='saved',path=str(folder));self.saved+=1
            except Exception as exc:
                with self.lock:
                    row.update(state='failed',error=str(exc));self.failed+=1

    def close(self):
        self.closed.set();self.worker.join(timeout=5)
