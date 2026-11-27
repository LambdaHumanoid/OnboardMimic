import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from onboardmimic_lab.dataset_io import digest, rows, write_json
from onboardmimic_lab.reference_dataset import RawEpisode, verify_raw, read_aligned
from onboardmimic_lab.raw_storage import compact_raw, raw_path
from onboardmimic_lab.compact_dataset import image_bytes
from onboardmimic_lab.export_jobs import finish_export
from onboardmimic_lab.reference_replay import prepare_plan
from onboardmimic_lab.video_timing import MpegTsPtsExtractor
from test_collection import make_video
from test_reference import spec, sample


class CompactStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);s=spec()
        video=make_video(root/'input.ts')
        raw=RawEpisode(root/'episode',dict(task='compact',controller_spec=s));raw.video(video)
        for i,pts in enumerate(MpegTsPtsExtractor().feed(video)):
            value=sample(s,i);raw.controller(value,value['timestamp_monotonic_ns'])
            raw.append('camera.jsonl',dict(sequence=i,pts_90khz=pts,monotonic_ns=value['timestamp_monotonic_ns']))
        raw.finish();self.root=raw.root
        self.original=(self.root/'controller.jsonl').read_bytes()

    def test_roundtrip_plan_images_and_no_duplicates(self):
        finish_export(self.root)
        manifest=verify_raw(self.root);meta,data=read_aligned(self.root)
        self.assertEqual(gzip.decompress(raw_path(self.root,'controller.jsonl').read_bytes()),self.original)
        self.assertFalse((self.root/'controller.jsonl').exists())
        self.assertFalse(list(self.root.rglob('*.jpg')))
        self.assertNotIn('sample',next(rows(self.root/'aligned/frames.jsonl')))
        self.assertEqual(len(data),20)
        plan=prepare_plan(self.root)
        self.assertEqual(plan['source_sha256'],manifest['files']['controller.jsonl']['sha256'])
        self.assertEqual([f['sample'] for f in plan['frames']],[r['sample'] for r in data])
        self.assertTrue(image_bytes(self.root,data[-1]).startswith(b'\xff\xd8'))
        path=raw_path(self.root,'controller.jsonl');path.write_bytes(path.read_bytes()[:-8])
        with self.assertRaises(ValueError):verify_raw(self.root)

    def test_retired_jpeg_layout_is_rejected(self):
        finish_export(self.root)
        summary_path=self.root/'aligned/episode.json'
        summary=json.loads(summary_path.read_text())
        summary['format']='onboardmimic_lab_aligned_v2'
        write_json(summary_path,summary)
        manifest=verify_raw(self.root)
        manifest['aligned_manifest']=digest(summary_path)
        write_json(self.root/'manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'aligned contract mismatch'):
            read_aligned(self.root)

    def test_failed_manifest_commit_preserves_original_and_retry(self):
        with patch('onboardmimic_lab.raw_storage.write_json',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):compact_raw(self.root)
        self.assertEqual((self.root/'controller.jsonl').read_bytes(),self.original)
        verify_raw(self.root)
        compact_raw(self.root);compact_raw(self.root)
        self.assertEqual(gzip.decompress(raw_path(self.root,'controller.jsonl').read_bytes()),self.original)

    def test_index_tamper_even_with_updated_hash_rejected(self):
        finish_export(self.root)
        path=self.root/'aligned/frames.jsonl';data=list(rows(path));data[0]['source_index']+=1
        path.write_text(''.join(json.dumps(r)+'\n' for r in data))
        summary=json.loads((self.root/'aligned/episode.json').read_text())
        summary['files']['frames.jsonl']=digest(path);write_json(self.root/'aligned/episode.json',summary)
        m=verify_raw(self.root);m['aligned_manifest']=digest(self.root/'aligned/episode.json');write_json(self.root/'manifest.json',m)
        with self.assertRaisesRegex(ValueError,'index does not match'):read_aligned(self.root)

    def test_interrupted_cleanup_uses_committed_gzip_then_retries(self):
        real_unlink=Path.unlink
        def interrupted(path, *args, **kwargs):
            if path == self.root/'controller.jsonl': raise OSError('cleanup interrupted')
            return real_unlink(path,*args,**kwargs)
        with patch.object(Path,'unlink',interrupted):
            with self.assertRaises(OSError):compact_raw(self.root)
        verify_raw(self.root)
        self.assertTrue((self.root/'controller.jsonl').exists())
        self.assertEqual(list(rows(raw_path(self.root,'controller.jsonl'))),
                         [json.loads(line) for line in self.original.splitlines()])
        compact_raw(self.root)
        self.assertFalse((self.root/'controller.jsonl').exists())
