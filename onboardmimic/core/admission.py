"""Startup admission errors are advisory for an already-owned controller."""
import time


class AdmissionCheckError(RuntimeError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


class RunningAdmissionObserver:
    """Report inventory failures without stopping or restarting control.

    This observer has no gateway/process reference. Hardware and transport
    protections remain the responsibility of the lifecycle and runtime.
    """
    def __init__(self, check, emit, clock=time.monotonic, interval=5.):
        self.check, self.emit, self.clock, self.interval = check, emit, clock, interval
        self.pid = None
        self.count = 0
        self.last_report = None

    def inspect(self, owned_pid):
        if owned_pid != self.pid:
            self.pid, self.count, self.last_report = owned_pid, 0, None
        if owned_pid is None:
            return
        try:
            self.check(owned_pid)
        except Exception as exc:
            self.count += 1
            now = self.clock()
            if self.last_report is None or now-self.last_report >= self.interval:
                self.last_report = now
                self.emit(dict(event='running_admission_warning',
                    action='keep_current_controller', owned_pid=owned_pid,
                    monotonic=now, wall_time=time.time(), occurrences=self.count,
                    error_type=type(exc).__name__, reason=str(exc)[:500],
                    details=getattr(exc, 'details', {})))
        else:
            if self.count:
                self.emit(dict(event='running_admission_recovered', owned_pid=owned_pid,
                    monotonic=self.clock(), wall_time=time.time(), occurrences=self.count))
                self.count, self.last_report = 0, None
