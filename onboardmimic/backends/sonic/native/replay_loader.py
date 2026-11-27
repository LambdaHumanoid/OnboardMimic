"""Isolated parsing/validation for large recorded plans; no robot interfaces."""
import json
import subprocess
import sys
import threading

from .replay_plan import validate


class PlanLoader:
    def __init__(self):
        self.lock = threading.Lock()

    def decode(self, payload):
        if not isinstance(payload, bytes) or not payload:
            raise ValueError('Invalid replay request size')
        if not self.lock.acquire(blocking=False):
            raise ValueError('Another replay plan is being validated')
        try:
            # Parsing and canonical JSON hashing hold the GIL for long periods.
            # A thread is insufficient: use a separate interpreter and return
            # only validated tokens, start pose and contract metadata.
            try:
                result = subprocess.run(
                    [sys.executable, '-m', __name__], input=payload,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            except subprocess.TimeoutExpired as exc:
                raise ValueError('Replay plan validation timed out') from exc
            if result.returncode or not result.stdout:
                raise ValueError('Replay plan validation worker failed')
            value = json.loads(result.stdout)
            if not value.get('ok'):
                raise ValueError(value.get('error', 'Invalid replay plan'))
            return value['plan']
        finally:
            self.lock.release()


def main():
    try:
        raw = sys.stdin.buffer.read()
        request = json.loads(raw)
        if not isinstance(request, dict): raise ValueError('Request must be an object')
        result = dict(ok=True, plan=validate(request['plan']))
    except (ValueError, TypeError, KeyError, IndexError, StopIteration, OverflowError) as exc:
        result = dict(ok=False, error=str(exc)[:2000])
    sys.stdout.write(json.dumps(result, allow_nan=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
