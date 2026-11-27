"""Bounded feedback inbox: socket reception never waits for the control lock."""
import threading
import time


class FeedbackInbox:
    def __init__(self, topics, clock=time.monotonic):
        self.topics = frozenset(topics) | {'rt/continuity'}
        self.lock = threading.Lock()
        self.pending = {}
        self.timing = {}
        self.replaced = 0
        self.clock = clock
        self.delivery = {}
        self.applied = {}

    def put(self, topic, value, received_at, *, decoded_at=None, decode_cpu_ns=None):
        if topic not in self.topics or not isinstance(value, dict):
            return
        entered = self.clock()
        with self.lock:
            enqueued = self.clock()
            old = self.timing.get(topic, {})
            previous = old.get('received_at')
            if previous is not None and received_at <= previous:
                return
            gap = None if previous is None else (received_at-previous)*1000
            self.timing[topic] = dict(received_at=received_at, receive_gap_ms=gap,
                max_receive_gap_ms=max(old.get('max_receive_gap_ms', 0), gap or 0))
            self.replaced += topic in self.pending
            self.pending[topic] = (received_at, value, dict(
                received_at=received_at,decoded_at=decoded_at,enqueued_at=enqueued,
                enqueue_lock_wait_ms=max(0.,(enqueued-entered)*1000),decode_cpu_ns=decode_cpu_ns))

    def drain(self):
        with self.lock:
            pending, self.pending = self.pending, {}
            drained = self.clock()
            self.delivery = {topic:dict(meta,drained_at=drained,
                queue_wait_ms=max(0.,(drained-meta['enqueued_at'])*1000))
                for topic,(_,_,meta) in pending.items()}
        return sorted(((topic, stamp, value) for topic, (stamp, value, _) in pending.items()),
                      key=lambda row: row[1])

    def mark_applied(self, topic, received_at, applied_at):
        with self.lock:
            meta = self.delivery.get(topic)
            if meta is not None and meta['received_at']==received_at:
                self.applied[topic] = dict(meta,applied_at=applied_at,
                    drain_to_apply_ms=max(0.,(applied_at-meta['drained_at'])*1000))

    def snapshot(self):
        with self.lock:
            return dict(replaced=self.replaced, pending_topics=list(self.pending),
                        topics={k: dict(v) for k, v in self.timing.items()},
                        applied={k: dict(v) for k,v in self.applied.items()})
