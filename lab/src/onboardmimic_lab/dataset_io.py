"""Shared durable JSON and integrity helpers."""
import gzip
import hashlib
import json
import os
from pathlib import Path

def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as f:
        json.dump(value, f, ensure_ascii=False, allow_nan=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            result.update(block)
    return {"bytes": Path(path).stat().st_size, "sha256": result.hexdigest()}


def rows(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as f:
        for line in f:
            yield json.loads(line)


def episode_manifest(root):
    root = Path(root)
    from .lerobot_storage import find
    if find(root) is not None:
        from .lerobot_dataset import manifest
        return manifest(root)
    return json.loads((root/'manifest.json').read_text())


def episode_metadata_paths(root):
    if not Path(root).is_dir(): return
    for directory in Path(root).iterdir():
        if not directory.is_dir(): continue
        path = directory/('episode.json' if (directory/'episode.json').exists() else 'manifest.json')
        if path.exists(): yield path


def read_episode_metadata(path):
    value = json.loads(Path(path).read_text())
    return value['source_manifest'] if Path(path).name == 'episode.json' else value
