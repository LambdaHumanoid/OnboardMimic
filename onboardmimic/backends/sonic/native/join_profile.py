"""Bounded slow-join attribution; no sampling threads or filesystem work."""
from collections import deque
import gc
import resource
import threading
import time


class JoinProfile:
    THRESHOLD_NS = 8_000_000

    def __init__(self, trace):
        self.trace = trace
        self.collections = deque(maxlen=32)
        self.collecting = {}
        self.parts = []
        self.callback = self._gc
        gc.callbacks.append(self.callback)

    def _gc(self, phase, info):
        # Capture collections from the audit thread as well: they also hold the
        # process GIL. Store only metadata, never enumerate the Python heap.
        tid = threading.get_ident()
        if phase == 'start':
            self.collecting[tid] = (time.monotonic_ns(),time.thread_time_ns(),info['generation'])
        elif phase == 'stop':
            start = self.collecting.pop(tid,None)
            if start:
                self.collections.append((start[0],time.monotonic_ns(),
                    time.thread_time_ns()-start[1],start[2],tid))

    def begin(self):
        self.parts.clear()
        self.start = self.wall = time.monotonic_ns()
        self.start_cpu = self.cpu = time.thread_time_ns()
        self.usage = resource.getrusage(resource.RUSAGE_THREAD)

    def mark(self, name):
        wall,cpu = time.monotonic_ns(),time.thread_time_ns()
        self.parts.append((name,wall-self.wall,cpu-self.cpu))
        self.wall,self.cpu = wall,cpu

    def finish(self, source, event):
        end = time.monotonic_ns()
        if end-self.start <= self.THRESHOLD_NS: return
        self.mark('remaining_ingest')
        end = self.wall
        usage = resource.getrusage(resource.RUSAGE_THREAD)
        collections = [dict(start_ns=a,end_ns=b,cpu_ns=c,generation=g,thread_id=tid)
            for a,b,c,g,tid in tuple(self.collections) if a<=end and b>=self.start]
        self.trace('slow_ingest',source=source,kind=event.get('kind'),index=event.get('index'),
            start_ns=self.start,end_ns=end,wall_ns=end-self.start,
            cpu_ns=self.cpu-self.start_cpu,
            voluntary_switches=usage.ru_nvcsw-self.usage.ru_nvcsw,
            involuntary_switches=usage.ru_nivcsw-self.usage.ru_nivcsw,
            stages=[dict(stage=n,wall_ns=w,cpu_ns=c) for n,w,c in self.parts],gc_events=collections,
            gc_history_truncated=bool(len(self.collections)==32 and self.collections[0][0]>self.start))

    def close(self):
        if self.callback in gc.callbacks: gc.callbacks.remove(self.callback)
