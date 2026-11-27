"""Small, throttled worker status; never part of the recorded dataset."""
import json
import time
from pathlib import Path


class ExportProgress:
    def __init__(self, root):
        self.path = Path(root)/'processing-progress.json'
        self.last_write = 0.
        self.stage = None

    def update(self, stage, completed=None, total=None):
        now = time.monotonic()
        if stage == self.stage and now-self.last_write < .5:
            return
        value = dict(stage=stage)
        if total and total > 0 and completed is not None:
            value.update(completed=int(completed), total=int(total),
                         percent=min(99, max(0, int(completed*100/total))))
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value))
        temporary.replace(self.path)
        self.last_write, self.stage = now, stage


def read_progress(root):
    try:
        value = json.loads((Path(root)/'processing-progress.json').read_text())
        if not isinstance(value, dict) or value.get('stage') not in ('verify_raw','video','verify_output','saving'):
            return None
        return value
    except (OSError, ValueError):
        return None
