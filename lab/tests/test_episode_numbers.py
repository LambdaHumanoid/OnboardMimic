import json
from pathlib import Path
import tempfile
import unittest
from onboardmimic_lab.tasks import episode_numbers, reserve_episode


class EpisodeNumbersTest(unittest.TestCase):
    def test_per_task_zero_based_persistent_and_discard_does_not_renumber(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            self.assertEqual(reserve_episode(root,'task-a','a0'),0)
            self.assertEqual(reserve_episode(root,'task-a','a1'),1)
            self.assertEqual(reserve_episode(root,'task-b','b0'),0)
            path=root/'discarded/a0';path.mkdir(parents=True)
            (path/'manifest.json').write_text(json.dumps(dict(task_id='task-a',episode_index=0)))
            self.assertEqual(reserve_episode(Path(d),'task-a','a2'),2)
            self.assertEqual(episode_numbers(root)['a1']['index'],1)

    def test_legacy_chronology_and_immutable_source(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);files={}
            for folder,eid,stamp in [('episodes','z',1),('pending','a',2),('discarded','b',3)]:
                path=root/folder/eid/'manifest.json';path.parent.mkdir(parents=True)
                raw=json.dumps(dict(task='old task',created_realtime_ns=stamp));path.write_text(raw);files[path]=raw
            rows=episode_numbers(root)
            self.assertEqual([rows[n]['index'] for n in ('z','a','b')],[0,1,2])
            self.assertEqual(reserve_episode(root,rows['z']['task_id'],'new'),3)
            self.assertEqual(episode_numbers(root)['z']['index'],0)
            for path,raw in files.items():self.assertEqual(path.read_text(),raw)
