"""Bounded FIFO draining of ready telemetry sockets; never discard evidence."""
import time


class ReceiveBatch:
    # Return to publication health/heartbeat/stop checks at least every batch.
    # A single decode/join may exceed the time budget; never interrupt validation.
    MAX_EVENTS = 64
    MAX_NS = 4_000_000

    def __init__(self, sockets, clock_ns=time.monotonic_ns):
        self.sockets = list(sockets)
        self.clock_ns = clock_ns
        self.first = 0
        self.counts = [0] * len(self.sockets)
        self.limit = None

    def receive(self, ready):
        import zmq
        self.counts = [0] * len(self.sockets)
        self.limit = None
        start = self.clock_ns()
        order = [(self.first+i) % len(self.sockets) for i in range(len(self.sockets))]
        self.first = (self.first+1) % len(self.sockets)
        active = [i for i in order if self.sockets[i] in ready]
        count = 0
        while active:
            for i in tuple(active):
                if count >= self.MAX_EVENTS or self.clock_ns()-start >= self.MAX_NS:
                    self.limit = 'events' if count >= self.MAX_EVENTS else 'time'
                    return
                # The first read uses the poll result. Subsequent reads check
                # local ZMQ readiness so normal empty queues do not allocate
                # an Again exception for each source on every 500 Hz tick.
                if self.counts[i] and not self.sockets[i].getsockopt(zmq.EVENTS) & zmq.POLLIN:
                    active.remove(i)
                    continue
                try:
                    frames = self.sockets[i].recv_multipart(zmq.NOBLOCK)
                except zmq.Again:
                    active.remove(i)
                    continue
                count += 1
                self.counts[i] += 1
                yield i, frames
        # An empty source is removed independently, so a busier source can catch
        # up without one poll/housekeeping cycle for each redundant writer packet.
