import unittest
from onboardmimic_lab.adapters.sonic_simulation import reference_schedule


class ReferenceScheduleTests(unittest.TestCase):
    def test_long_gap_short_burst_keeps_source_and_all_frames(self):
        offsets=[0,42_000_000,45_000_000,65_000_000]
        frames=[dict(offset_ns=t) for t in offsets]
        self.assertEqual(reference_schedule(frames),[0,20_000_000,40_000_000,60_000_000])
        self.assertEqual([f['offset_ns'] for f in frames],offsets)

    def test_dense_source_is_played_once_per_tick(self):
        self.assertEqual(reference_schedule([dict(offset_ns=i*1_000_000) for i in range(10)]),
                         [i*20_000_000 for i in range(10)])

    def test_long_recording_preserves_every_token(self):
        schedule=reference_schedule([{}]*14461)
        self.assertEqual(len(schedule),14461)
        self.assertEqual(schedule[-1],14460*20_000_000)
