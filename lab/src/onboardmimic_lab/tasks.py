"""Task metadata shared by capture and replay; episode files stay immutable."""
import hashlib
import json
import time
import uuid
from pathlib import Path
from .dataset_io import write_json, episode_metadata_paths, read_episode_metadata
from .lerobot_storage import entries as lerobot_entries


def identity(metadata):
    description = metadata.get('task_description', metadata.get('task', ''))
    task_id = metadata.get('task_id')
    if not task_id:
        task_id = 'legacy-' + hashlib.sha256(json.dumps(description, ensure_ascii=False).encode()).hexdigest()[:24]
    return dict(id=task_id, name=metadata.get('task_name', description), description=description)


class Tasks:
    def __init__(self, root):
        self.root = Path(root)
        (self.root/'tasks').mkdir(exist_ok=True)

    def list(self):
        records = {}
        for entry in lerobot_entries(self.root):
            row=identity(entry['om']['source_manifest']);records[row['id']]=row
        for folder in ('episodes', 'pending', 'discarded'):
            for path in episode_metadata_paths(self.root/folder):
                try:
                    row = identity(read_episode_metadata(path))
                    records[row['id']] = row
                except (ValueError, OSError, TypeError):
                    continue
        for path in sorted((self.root/'tasks').glob('*.json')):
            row = json.loads(path.read_text());row.setdefault('name',row.get('description',''));records[row['id']] = row
        return list(records.values())

    def resolve(self, request):
        task_id = request.get('task_id')
        if task_id:
            for row in self.list():
                if row['id'] == task_id:
                    return row
            raise ValueError('unknown task id')
        description = request.get('task_description', request.get('task', ''))
        name = request.get('task_name', '')
        if not isinstance(name, str) or len(name) > 100:
            raise ValueError('invalid task name')
        if not isinstance(description, str) or len(description) > 500:
            raise ValueError('invalid task metadata')
        row = dict(id=uuid.uuid4().hex, name=name.strip(), description=description.strip(), created_realtime_ns=time.time_ns())
        write_json(self.root/'tasks'/(row['id']+'.json'), row)
        return row


def episode_numbers(root, *, saved_entries=None):
    """Stable task-local numbers, including discarded and pending captures."""
    root = Path(root); path = root/'episode-numbers.json'
    numbers = json.loads(path.read_text()) if path.exists() else {}
    if saved_entries is None: saved_entries = lerobot_entries(root)
    records = [(entry['om']['source_manifest'].get('created_realtime_ns',0),entry['om']['id'],entry['om']['source_manifest']) for entry in saved_entries]
    for folder in ('episodes', 'pending', 'discarded'):
        for manifest in episode_metadata_paths(root/folder):
            try:
                metadata = read_episode_metadata(manifest)
                records.append((metadata.get('created_realtime_ns', 0), manifest.parent.name, metadata))
            except (ValueError, OSError):
                continue
    for _, eid, metadata in records:
        index = metadata.get('episode_index')
        if type(index) is int and index >= 0:
            numbers[eid] = dict(task_id=identity(metadata)['id'], index=index)
    next_index = {}
    for row in numbers.values():
        next_index[row['task_id']] = max(next_index.get(row['task_id'], 0), row['index']+1)
    for _, eid, metadata in sorted(records, key=lambda r:(r[0],r[1])):
        if eid in numbers:continue
        tid = identity(metadata)['id'];index = next_index.get(tid, 0)
        numbers[eid] = dict(task_id=tid, index=index);next_index[tid] = index+1
    return numbers


def reserve_episode(root, task_id, episode_id):
    numbers = episode_numbers(root)
    index = max((r['index'] for r in numbers.values() if r['task_id']==task_id), default=-1)+1
    numbers[episode_id] = dict(task_id=task_id, index=index)
    write_json(Path(root)/'episode-numbers.json', numbers)
    return index
