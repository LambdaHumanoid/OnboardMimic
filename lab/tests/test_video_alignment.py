import json
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from onboardmimic_lab.video_alignment import aligned_video, decoded_camera


class VideoAlignmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'manifest.json').write_text('{}')

    def receipts(self, entries):
        (self.root/'camera.jsonl').write_text(''.join(json.dumps(dict(
            sequence=i, pts_90khz=pts, monotonic_ns=stamp))+'\n'
            for i, (pts, stamp) in enumerate(entries)))

    def frames(self, pts):
        return [SimpleNamespace(pts=p, time_base=Fraction(1, 90000)) for p in pts]

    def align(self, stamps, frames):
        samples = [dict(sample=dict(timestamp_monotonic_ns=s, reference_evidence='consumed'),
                        segment_index=0, received_monotonic_ns=s+1) for s in stamps]
        stats = {}
        with patch('onboardmimic_lab.video_alignment.samples', return_value=iter(samples)):
            result = list(aligned_video(self.root, iter(frames), {}, stats))
        return result, stats

    def test_missing_decoded_receipt_reuse_and_gap(self):
        self.receipts([(10, 10), (20, 20), (30, 100_000_000)])
        result, stats = self.align([0, 10, 20, 90_000_000, 100_000_000], self.frames([10, 30]))
        self.assertEqual([r['camera_sequence'] for r in result], [0, 0, 2])
        self.assertEqual([r['segment_index'] for r in result], [0, 0, 1])
        self.assertEqual(stats, dict(rows=3, rejected_rows=2, segments=2))
        self.assertIs(result[0]['image']['frame'], result[1]['image']['frame'])
        self.assertEqual(result[1]['received_monotonic_ns'], 21)

    def test_equal_ingress_clocks_choose_last_and_pts_wrap(self):
        self.receipts([((1 << 33)-1, 10), (0, 10)])
        result, _ = self.align([10], self.frames([-1, 0]))
        self.assertEqual(result[0]['camera_sequence'], 1)

    def test_duplicate_decoded_tail_is_rejected(self):
        self.receipts([(10, 10), (20, 20)])
        with self.assertRaisesRegex(ValueError, 'ambiguous decoded'):
            self.align([10], self.frames([10, 20, 30, 30]))

    def test_missing_pts_in_tail_is_rejected(self):
        self.receipts([(10, 10)])
        with self.assertRaisesRegex(ValueError, 'missing source frame PTS'):
            self.align([10], self.frames([10, 20, None]))

    def test_receipt_and_decode_regressions_rejected(self):
        for receipts, pts, error in [
            ([(10, 10), (10, 20)], [10], 'repeated camera PTS'),
            ([(10, 20), (20, 10)], [10, 20], 'invalid camera ingress'),
            ([(10, 10), (20, 20)], [20, 10], 'source order regressed'),
        ]:
            with self.subTest(receipts=receipts, pts=pts):
                self.receipts(receipts)
                with self.assertRaisesRegex(ValueError, error):
                    list(decoded_camera(self.root, self.frames(pts)))
