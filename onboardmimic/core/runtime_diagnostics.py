"""Bounded process-local timing evidence; never changes control or GC policy."""
from collections import deque
import gc
import os
from pathlib import Path
import sys
import threading
import time


class RuntimeDiagnostics:
    def __init__(self):
        self.phases = {}
        self.slow = deque(maxlen=64)
        self.collections = deque(maxlen=32)
        self.scheduling = deque(maxlen=32)
        self.collecting = {}
        self.callback = self._gc
        gc.callbacks.append(self.callback)

    def mark(self, role, stage):
        now, cpu = time.monotonic_ns(), time.thread_time_ns()
        tid = threading.get_native_id()
        previous = self.phases.get(role)
        if previous and previous[0] == tid:
            _, old_stage, began, old_cpu = previous
            if now-began >= 20_000_000 and old_stage not in ('wait', 'poll'):
                self.slow.append((role, old_stage, tid, began, now, cpu-old_cpu))
        self.phases[role] = (tid, stage, now, cpu)

    def _gc(self, phase, info):
        tid = threading.get_native_id()
        if phase == 'start':
            # Function/line only, no locals, tokens, payloads or heap enumeration.
            frames = []
            frame = sys._getframe(1)
            for _ in range(6):
                if frame is None: break
                frames.append((frame.f_code.co_filename, frame.f_code.co_name, frame.f_lineno))
                frame = frame.f_back
            self.collecting[tid] = (time.monotonic_ns(), time.thread_time_ns(),
                                    info['generation'], tuple(frames))
        elif phase == 'stop':
            start = self.collecting.pop(tid, None)
            if start:
                end, cpu = time.monotonic_ns(), time.thread_time_ns()
                if end-start[0] >= 1_000_000:
                    self.collections.append((tid, start[0], end, cpu-start[1], start[2], start[3]))

    def sample_scheduler(self):
        # Called by the existing 100 ms monitor, never by receive/control/GC.
        # These are asynchronous cumulative samples, not exact phase boundaries.
        for role, (tid, stage, began, _) in tuple(self.phases.items()):
            at = time.monotonic_ns()
            try:
                values = tuple(int(v) for v in Path('/proc', str(os.getpid()), 'task',
                                                   str(tid), 'schedstat').read_text().split())
                if len(values) != 3: raise ValueError('Invalid schedstat')
                row = (role, tid, stage, began, at, time.monotonic_ns(), values, '')
            except (OSError, ValueError) as exc:
                row = (role, tid, stage, began, at, time.monotonic_ns(), None, type(exc).__name__)
            self.scheduling.append(row)

    def snapshot(self):
        return dict(clock='same_host_monotonic_ns', sampled_ns=time.monotonic_ns(),
            automatic_gc_enabled=gc.isenabled(), gc_counts=gc.get_count(),
            phases={role:dict(thread_id=tid,stage=stage,since_ns=at,thread_cpu_ns=cpu)
                    for role,(tid,stage,at,cpu) in tuple(self.phases.items())},
            slow_phases=[dict(role=role,stage=stage,thread_id=tid,start_ns=a,end_ns=b,
                              thread_cpu_ns=cpu) for role,stage,tid,a,b,cpu in tuple(self.slow)],
            gc_events=[dict(thread_id=tid,start_ns=a,end_ns=b,thread_cpu_ns=cpu,
                            generation=g,trigger_stack=stack)
                       for tid,a,b,cpu,g,stack in tuple(self.collections)],
            scheduler=[dict(role=role,thread_id=tid,stage=stage,stage_since_ns=began,
                            sample_start_ns=a,sample_end_ns=b,counters=values,error=error)
                       for role,tid,stage,began,a,b,values,error in tuple(self.scheduling)])

    def close(self):
        if self.callback in gc.callbacks: gc.callbacks.remove(self.callback)
