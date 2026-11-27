"""Spike evidence retained independently of rotating logs, on the audit worker only.

Decoder >10 ms or control entry to inference completion >20 ms starts a fixed
200 ms before/after receive-time window. Telemetry receive or join delay above
40 ms also triggers retention, independently of inference speed or frame gaps. Adjacent triggers share that window;
sustained spikes create further windows rather than extending memory forever.
No control thresholds or sample continuity decisions are changed. The pre-ring
is capped at 4096 compact rows / ~4 MiB with explicit truncation reporting.
Archives in timing-incidents are not automatically deleted. Audit queue drops
remain in every row; shutdown can save an explicitly incomplete post-window.
"""
from collections import deque
import json
import os
from pathlib import Path
import uuid


class SpikeRecorder:
    WINDOW_NS = 200_000_000
    DECODER_NS = 10_000_000
    CONTROL_NS = 20_000_000
    TELEMETRY_NS = 40_000_000
    MAX_PRE_BYTES = 4 * 1024 * 1024
    MAX_PRE_ROWS = 4096

    def __init__(self, root):
        self.root = Path(root) / 'timing-incidents'
        self.pre = deque()
        self.pre_bytes = 0
        self.evicted_at = None
        self.active = None
        self.latest = None

    @classmethod
    def trigger(cls, row):
        # Queueing can break recording freshness while inference remains fast.
        # Trigger before the 60 ms readiness limit, without changing that limit.
        if row.get('stage') == 'slow_ingest':
            return dict(event=row,kind='slow_ingest',delay_ns=row['wall_ns'])
        if row.get('stage') == 'receiver_loop':
            # Exclude intentional idle poll time; report slow processing or a
            # pause between batches, even without an individual slow join.
            active, gap = row.get('max_active_batch_ns',0), row.get('max_between_batches_ns',0)
            # A bounded batch may legitimately include a whole join plus GIL
            # wakeups. Archive only > one inference period, not routine 8 ms
            # batches; all 100 ms phase summaries remain in the rolling log.
            if active > 20_000_000 or gap > 20_000_000:
                return dict(event=row,kind='slow_receive_loop',delay_ns=max(active,gap))
        if row.get('stage') == 'received':
            at, published = row.get('received_ns',row.get('at_ns')), row.get('published_monotonic_ns')
            if isinstance(at,int) and isinstance(published,int) and 0 < published <= at and at-published > cls.TELEMETRY_NS:
                return dict(event=row, kind='telemetry_receive_delay', delay_ns=at-published)
        if row.get('stage') == 'joined':
            at, source = row.get('at_ns'), row.get('source_ns')
            if isinstance(at,int) and isinstance(source,int) and 0 < source <= at and at-source > cls.TELEMETRY_NS:
                return dict(event=row, kind='telemetry_join_delay', delay_ns=at-source)
        if row.get('stage') != 'received' or row.get('source') != 'native' or row.get('kind') != 0:
            return None
        start, end = row.get('source_ns'), row.get('finished_ns')
        if not isinstance(start, int) or not isinstance(end, int) or not 0 < start <= end:
            return None
        stages = (row.get('timing') or {}).get('control_stages_ns') or []
        control = end - stages[0] if stages and isinstance(stages[0], int) and 0 < stages[0] <= start else None
        if end - start > cls.DECODER_NS or (control is not None and control > cls.CONTROL_NS):
            return dict(event=row, decoder_wall_ns=end-start, control_wall_ns=control)
        return None

    def observe(self, row, line):
        """Input is the compact audit row, never the full state/history sample."""
        now = row['at_ns']
        try:
            if self.active and now > self.active['deadline_ns']:
                self.finish(True)
            trigger = self.trigger(row)
            if trigger and not self.active:
                name = uuid.uuid4().hex
                folder = self.root / name
                folder.mkdir(parents=True, exist_ok=False)
                fd = os.open(self.root, os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
                self.latest = dict(id=name, state='saving')
                meta = dict(schema=1, id=name, state='saving', triggers=[trigger],
                            decoder_threshold_ns=self.DECODER_NS, control_threshold_ns=self.CONTROL_NS, telemetry_threshold_ns=self.TELEMETRY_NS,
                            window_before_ns=self.WINDOW_NS, window_after_ns=self.WINDOW_NS,
                            window_clock='audit_receive_monotonic_ns',
                            pre_window_truncated=self.evicted_at is not None and self.evicted_at >= now-self.WINDOW_NS,
                            deadline_ns=now+self.WINDOW_NS, folder=folder)
                self.active = meta
                meta['file'] = (folder/'events.jsonl').open('w')
                self._snapshot()
                trigger = None
                for at, previous in self.pre:
                    if at >= now-self.WINDOW_NS: meta['file'].write(previous)
            if self.active:
                if trigger: self.active['triggers'].append(trigger)
                # Do not retain giant join_gap samples; their own archive handles them.
                if row.get('stage') in ('received', 'joined', 'publish_attempt', 'inference_transition', 'slow_ingest', 'receiver_loop'):
                    self.active['file'].write(line)
        except Exception as exc:
            self._failed(exc)
        if row.get('stage') in ('received', 'joined', 'publish_attempt', 'inference_transition', 'slow_ingest', 'receiver_loop'):
            self.pre.append((now, line)); self.pre_bytes += len(line)
        while self.pre and (self.pre[0][0] < now-self.WINDOW_NS or len(self.pre)>self.MAX_PRE_ROWS or self.pre_bytes>self.MAX_PRE_BYTES):
            at, old = self.pre.popleft(); self.pre_bytes -= len(old); self.evicted_at = at

    def _snapshot(self):
        a = self.active
        payload = {k:v for k,v in a.items() if k not in ('file','folder')}
        tmp = a['folder']/'snapshot.tmp'
        with tmp.open('w') as f:
            json.dump(payload, f); f.flush(); os.fsync(f.fileno())
        tmp.replace(a['folder']/'snapshot.json')
        fd = os.open(a['folder'], os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)

    def _failed(self, exc):
        self.latest = dict(self.latest or {}, state='failed', error=str(exc))
        if self.active:
            try: self.active['file'].close()
            except Exception: pass
        self.active = None

    def finish(self, complete=False):
        if not self.active: return
        try:
            a = self.active
            a['file'].flush(); os.fsync(a['file'].fileno()); a['file'].close()
            a.update(state='saved', post_window_complete=complete)
            self._snapshot()
            self.latest = dict(id=a['id'],state='saved',post_window_complete=complete)
            self.active = None
        except Exception as exc:
            self._failed(exc)
