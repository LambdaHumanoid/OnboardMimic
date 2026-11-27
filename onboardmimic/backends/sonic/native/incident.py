"""Bounded replay incident retention; filesystem work never holds control locks."""
import json
import os
from pathlib import Path
import shutil
import threading
import time

_lock = threading.Lock()


def save_incident(metadata, directory=None):
    # Metadata is already detached from live state by the caller.
    if not _lock.acquire(blocking=False):
        return None
    name = 'replay-{}-{}'.format(metadata.get('run', 0), time.monotonic_ns())
    root = Path(directory or os.environ.get('OM_SONIC_AUDIT_DIR', 'var/logs'))
    def write():
        try:
            folder = root/'replay-incidents'/name
            folder.mkdir(parents=True, exist_ok=False)
            (folder/'snapshot.json').write_text(json.dumps(metadata, indent=2, allow_nan=False))
            # Only bounded live/rotated audit files, never recursively copy incidents.
            errors = []
            for source in root.glob('consumption-*.jsonl*'):
                if source.name.endswith(('.jsonl', '.jsonl.1')):
                    try: shutil.copyfile(source, folder/source.name)
                    except OSError as exc: errors.append(dict(file=source.name,error=str(exc)))
            (folder/'copy-result.json').write_text(json.dumps(dict(errors=errors)))
            # Keep the latest three incidents; ordinary traffic cannot overwrite them.
            for old in sorted(folder.parent.glob('replay-*'), key=lambda p:p.stat().st_mtime)[:-3]:
                if old.is_dir() and not old.is_symlink(): shutil.rmtree(old)
        except Exception:
            import logging
            logging.getLogger('runtime_audit').exception('Replay incident persistence failed: %s', name)
        finally:
            _lock.release()
    threading.Thread(target=write, name='replay-incident', daemon=True).start()
    return name
