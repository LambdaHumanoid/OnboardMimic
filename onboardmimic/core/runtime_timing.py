"""Monotonic waits for JP5/Python 3.8, whose timed semaphore uses wall time."""
import time


def wait_for(predicate, timeout):
    deadline = time.monotonic() + max(0., timeout)
    while not predicate():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        # A relative sleep is not extended by an NTP wall-clock step. Limit the
        # cancellation latency without using Event.wait/Lock.acquire(timeout).
        time.sleep(min(.05, remaining))
    return True


def wait_event(event, timeout):
    return wait_for(event.is_set, timeout)


def join_thread(thread, timeout):
    finished = wait_for(lambda: not thread.is_alive(), timeout)
    if finished:
        thread.join(0)
    return finished
