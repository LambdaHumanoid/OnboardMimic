"""Create owned children from a persistent thread, never an HTTP request thread."""
from concurrent.futures import Future
import queue
import threading


class ProcessLauncher:
    def __init__(self, spawn):
        self.spawn = spawn
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.closed = False
        self.thread = threading.Thread(target=self._run, name='owned-process-launcher', daemon=True)
        self.thread.start()

    def _run(self):
        while True:
            future = self.queue.get()
            if future is None:
                return
            try:
                future.set_result(self.spawn())
            except BaseException as exc:
                future.set_exception(exc)

    def __call__(self):
        with self.lock:
            if self.closed:
                raise RuntimeError('Process launcher is closed')
            future = Future()
            self.queue.put(future)
        # Do not time out and leave a child running without a lifecycle owner.
        return future.result()

    def close(self):
        # Caller must stop the owned child before ending its creating thread.
        with self.lock:
            if not self.closed:
                self.closed = True
                self.queue.put(None)
        self.thread.join()
