import importlib.util
import tempfile
from pathlib import Path
import unittest

from onboardmimic.backends.sonic import check_prebuilt as check

class PrebuiltTest(unittest.TestCase):
    def facts(self):
        return dict(arch='aarch64',ubuntu='20.04',l4t='35.3.1',cuda_runtime=11040,gpu='Orin',tensorrt='8.5.2-1+cuda11.4',board='NVIDIA Jetson Orin NX Developer Kit',memory_kib=15*1024*1024)
    def test_supported_environment(self):self.assertFalse(check.environment_errors(self.facts()))
    def test_unsupported_versions_and_board_rejected(self):
        for name,value in [('arch','x86_64'),('l4t','36.3.0'),('tensorrt','10.7.0'),('gpu','NVIDIA RTX 5090'),('memory_kib',8*1024*1024),('cuda_runtime',12000),('cuda_runtime',11030),('ubuntu','22.04'),('tensorrt','8.6.2')]:
            facts=self.facts();facts[name]=value
            with self.subTest(name=name):self.assertTrue(check.environment_errors(facts))
    def test_compatible_jp5_variants_are_admitted(self):
        for l4t in ('35.3.1','35.4.1','35.5.0','35.6.2'):
            for cuda in (11040,11070,11080):
                for board in ('Custom G1 carrier','', 'NVIDIA Jetson Orin NX Developer Kit'):
                    with self.subTest(l4t=l4t,cuda=cuda,board=board):
                        facts=dict(self.facts(),l4t=l4t,cuda_runtime=cuda,board=board,gpu='NVIDIA Orin',tensorrt='1:8.5.2.2-2+cuda11.4')
                        self.assertFalse(check.environment_errors(facts))
        self.assertFalse(check.environment_notes(self.facts()))
        self.assertTrue(check.environment_notes(dict(self.facts(),l4t='35.6.2')))

    def test_missing_changed_and_traversal_files_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'file';p.write_bytes(b'original')
            manifest={'files':{'file':{'size':8,'sha256':check.sha256(p)}}}
            check.verify_files(root,manifest)
            p.write_bytes(b'altered!')
            with self.assertRaises(ValueError):check.verify_files(root,manifest)
            p.unlink()
            with self.assertRaises(ValueError):check.verify_files(root,manifest)
            with self.assertRaises(ValueError):check.verify_files(root,{'files':{'../file':{'size':0,'sha256':''}}})
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'real').write_bytes(b'a');(root/'link').symlink_to('real')
            with self.assertRaises(ValueError):check.verify_files(root,{'files':{'link':{'size':1,'sha256':check.sha256(root/'real')}}})

if __name__=='__main__':unittest.main()
