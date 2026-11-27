"""Read explicit local simulation settings without discovering an OM runtime."""
import json
from pathlib import Path


def read_settings(path, required):
    path = Path(path).resolve(); config = json.loads(path.read_text())
    for key in required:
        value = Path(config[key]).expanduser()
        config[key] = str((path.parent/value).resolve())
    return config

