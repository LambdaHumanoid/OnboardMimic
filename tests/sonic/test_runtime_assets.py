import hashlib,tempfile,unittest
from pathlib import Path
from onboardmimic.backends.sonic.runtime_assets import validate_caches,CACHES
class RuntimeAssetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        for model,cache in CACHES:
            p=self.root/model;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'model')
            d=hashlib.sha256(b'model').hexdigest();d=hashlib.sha256((d+'test-gpu').encode()).hexdigest();header=hashlib.sha256((d+'0').encode()).hexdigest().encode()
            (self.root/cache).write_bytes(header+b'engine')
    def test_matching_caches(self):self.assertTrue(validate_caches(self.root,'test-gpu')['validated'])
    def test_missing_cache_rejected(self):
        (self.root/CACHES[0][1]).unlink()
        with self.assertRaisesRegex(RuntimeError,'缺少'):validate_caches(self.root,'test-gpu')
    def test_wrong_gpu_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'不匹配'):validate_caches(self.root,'different-gpu')
    def test_changed_model_rejected(self):
        (self.root/CACHES[0][0]).write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError,'不匹配'):validate_caches(self.root,'test-gpu')
    def test_truncated_rejected(self):
        (self.root/CACHES[0][1]).write_bytes(b'0'*64)
        with self.assertRaisesRegex(RuntimeError,'截断'):validate_caches(self.root,'test-gpu')
