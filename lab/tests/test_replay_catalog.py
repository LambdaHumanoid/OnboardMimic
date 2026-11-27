"""List polling must not repeatedly parse the dataset-wide index."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from onboardmimic_lab.replay_console import ReplayConsole


class ReplayCatalogTest(unittest.TestCase):
    def test_single_read_fresh_snapshot_and_path_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'meta').mkdir()
            (root/'episodes').mkdir()
            index = root/'meta/episodes.jsonl'
            def entry(name):
                return {'om': {'id': name, 'frames': 100, 'source_manifest': {
                    'controller_spec': {'schema_version': 2, 'period_ns': 20000000},
                    'task_id': 'task', 'task': 'test', 'created_realtime_ns': 1}}}
            rows = [entry('episode'+str(i)) for i in range(16)]
            def save(): index.write_text('\n'.join(json.dumps(row) for row in rows))
            save()
            console = ReplayConsole(root)
            original = Path.read_text
            reads = []
            def read(path, *args, **kwargs):
                if path == index: reads.append(path)
                return original(path, *args, **kwargs)
            with patch.object(Path, 'read_text', read):
                status = console.status()
            self.assertEqual(len(reads), 1)
            self.assertEqual(len(status['episodes']), 16)
            rows.append(entry('new_episode')); save()
            self.assertEqual(len(console.status()['episodes']), 17)
            (root/'episodes'/'episode0').symlink_to(root/'meta', target_is_directory=True)
            result = console.status()
            self.assertEqual(result['invalid_episodes'], 1)
            self.assertNotIn('episode0', [row['id'] for row in result['episodes']])
