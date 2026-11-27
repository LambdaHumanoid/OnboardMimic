"""Compact 100 ms receiver windows, including waiting and other Python threads.

No file access or new threads on the receive path. Counters are bounded; exact
packet/inference evidence continues to be recorded by the existing audit trail.
"""
import resource
import gc
import threading
import time


class ReceiverProfile:
    WINDOW_NS = 100_000_000

    def __init__(self, trace, join_profile):
        self.trace = trace
        self.join_profile = join_profile
        self.thread_id = threading.get_native_id()
        self.wall = time.monotonic_ns()
        self.cpu = time.thread_time_ns()
        self._reset()

    def _reset(self):
        self.start, self.start_cpu = self.wall, self.cpu
        self.process_cpu = time.process_time_ns()
        self.usage = resource.getrusage(resource.RUSAGE_THREAD)
        self.stages = {}
        self.counts = [0, 0]
        self.delays = [0, 0]
        self.cycles = self.limited = 0
        self.max_active = self.max_between = 0

    def mark(self, stage):
        wall, cpu = time.monotonic_ns(), time.thread_time_ns()
        elapsed = wall-self.wall
        totals = self.stages.get(stage)
        if totals is None:
            totals = self.stages[stage] = [0, 0, 0]
        totals[0] += elapsed
        totals[1] += cpu-self.cpu
        totals[2] = max(totals[2], elapsed)
        self.wall, self.cpu = wall, cpu
        return elapsed

    def begin(self):
        self.max_between = max(self.max_between, self.mark('between_batches'))
        self.batch_start = self.wall
        self.poll_ns = 0

    def polled(self):
        self.poll_ns += self.mark('poll')

    def received(self, source, event, received_ns):
        self.counts[source] += 1
        published = event.get('published_monotonic_ns')
        if type(published) is int and 0 < published <= received_ns:
            self.delays[source] = max(self.delays[source], received_ns-published)

    def finish(self, batch):
        self.mark('batch_finish')
        self.cycles += 1
        self.limited += bool(batch.limit)
        self.max_active = max(self.max_active, self.wall-self.batch_start-self.poll_ns)
        if self.wall-self.start < self.WINDOW_NS:
            return None
        usage = resource.getrusage(resource.RUSAGE_THREAD)
        gc_events = [dict(start_ns=a,end_ns=b,cpu_ns=c,generation=g,thread_id=tid)
            for a,b,c,g,tid in tuple(self.join_profile.collections)
            if a <= self.wall and b >= self.start]
        row = dict(start_ns=self.start,end_ns=self.wall,thread_id=self.thread_id,
            automatic_gc_enabled=gc.isenabled(),gc_counts=gc.get_count(),
            wall_ns=self.wall-self.start,cpu_ns=self.cpu-self.start_cpu,
            process_cpu_ns=time.process_time_ns()-self.process_cpu,
            voluntary_switches=usage.ru_nvcsw-self.usage.ru_nvcsw,
            involuntary_switches=usage.ru_nivcsw-self.usage.ru_nivcsw,
            cycles=self.cycles,batch_limit_hits=self.limited,
            packets=dict(zip(('native','bridge'),self.counts)),
            max_receive_delay_ns=dict(zip(('native','bridge'),self.delays)),
            max_active_batch_ns=self.max_active,max_between_batches_ns=self.max_between,
            stages={k:dict(wall_ns=v[0],cpu_ns=v[1],max_wall_ns=v[2]) for k,v in self.stages.items()},
            gc_events=gc_events,
            gc_history_truncated=bool(len(self.join_profile.collections)==32 and
                                      self.join_profile.collections[0][0]>self.start))
        # IPC carries only the small summary. Detailed phase/GC data stays in the
        # asynchronous journal and timing-incident archive.
        summary={k:v for k,v in row.items() if k not in ('stages','gc_events','gc_history_truncated')}
        self._reset()
        self.trace('receiver_loop',**row)
        return summary
