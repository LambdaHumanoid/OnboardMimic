import json
import time
from pathlib import Path


def wait_export(path, timeout=10):
    path = Path(path)
    until = time.monotonic()+timeout
    while time.monotonic() < until:
        from onboardmimic_lab.dataset_io import episode_manifest
        value = episode_manifest(path)
        if value.get('status') == 'saved': return value
        if value.get('export', {}).get('state') == 'failed':
            raise AssertionError(value['export'])
        time.sleep(.01)
    raise AssertionError(value)
