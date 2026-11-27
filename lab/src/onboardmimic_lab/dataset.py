"""Reference capture and LeRobot dataset API; unsupported formats fail explicitly."""
import json
from pathlib import Path
from .dataset_io import write_json, digest, rows
from .reference_dataset import RawEpisode, FORMAT, FILES, align


def implementation(root):
    from .lerobot_storage import find
    if find(root) is not None:
        from . import lerobot_dataset
        return lerobot_dataset
    value = json.loads((Path(root)/'manifest.json').read_text())
    if value.get('format') == 'onboardmimic_lab_multimodal_v2':
        from . import reference_dataset
        return reference_dataset
    raise ValueError('Unsupported dataset format; use reference capture and LeRobot v2.1')


def verify_raw(root): return implementation(root).verify_raw(root)
def export_episode(root): return implementation(root).export_episode(root)
def read_aligned(root): return implementation(root).read_aligned(root)
