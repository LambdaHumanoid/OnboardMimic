"""Reject stale or foreign native artifacts before package generation."""
import importlib.util,tempfile,unittest
from pathlib import Path
from onboardmimic.backends.sonic import build_release as m
class NativePackageTest(unittest.TestCase):
    def test_loader_only_reports_cannot_qualify_cpp_startup(self):
        for report in ({},{'validation':{'arm_dynamic_loader':'passed'}},
                       {'validation':{'cxx_static_initialization':'not run'}}):
            with self.subTest(report=report),self.assertRaises(SystemExit):m.verify_startup_report(report)
        m.verify_startup_report({'validation':{'cxx_static_initialization':'passed'}})

    def test_only_report_matched_arm_binary_and_current_sources_pass(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'source';source.mkdir();(source/'controller.cpp').write_text('source')
            binary=root/'controller';header=bytearray(20);header[:6]=b'\x7fELF\x02\x01';header[18:20]=(183).to_bytes(2,'little');binary.write_bytes(header)
            hashes={'controller.cpp':m.digest(source/'controller.cpp')}
            report={'architecture':'aarch64','binary_sha256':m.digest(binary),'native_source_hashes':hashes}
            m.verify_native_build(binary,report,source,hashes)
            for key,value in [('binary_sha256','old'),('architecture','x86_64'),('native_source_hashes',{})]:
                with self.subTest(key=key),self.assertRaises(SystemExit):m.verify_native_build(binary,{**report,key:value},source,hashes)
            (source/'controller.cpp').write_text('different')
            with self.assertRaises(SystemExit):m.verify_native_build(binary,report,source,hashes)
            (source/'controller.cpp').write_text('source')
            header[18:20]=(62).to_bytes(2,'little');binary.write_bytes(header)
            with self.assertRaises(SystemExit):m.verify_native_build(binary,{**report,'binary_sha256':m.digest(binary)},source,hashes)
    def test_bridge_requires_both_arm_binaries_and_exact_source_report(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'source';source.mkdir();build=root/'build';build.mkdir()
            for name in ('core.hpp','core_test.cpp','sonic_command_bridge.cpp','sonic_state_reader.cpp','CMakeLists.txt','build.sh'):
                (source/name).write_text(name)
            header=bytearray(20);header[:6]=b'\x7fELF\x02\x01';header[18:20]=(183).to_bytes(2,'little')
            names=('sonic_command_bridge','sonic_state_reader')
            for name in names:(build/name).write_bytes(header)
            report={'architecture':'aarch64','source_hashes':{p.name:m.digest(p) for p in source.iterdir()},
                    'binary_sha256':{name:m.digest(build/name) for name in names}}
            m.verify_bridge_build(build,report,source)
            with self.assertRaises(SystemExit):m.verify_bridge_build(build,{**report,'source_hashes':{}},source)
            for name in names:
                with self.subTest(binary=name):
                    (build/name).write_bytes(header+b'changed')
                    with self.assertRaises(SystemExit):m.verify_bridge_build(build,report,source)
                    (build/name).write_bytes(header)
            (source/'core.hpp').write_text('changed source')
            with self.assertRaises(SystemExit):m.verify_bridge_build(build,report,source)
            (source/'core.hpp').write_text('core.hpp')
            (build/names[0]).unlink()
            with self.assertRaises(FileNotFoundError):m.verify_bridge_build(build,report,source)

if __name__=='__main__':unittest.main()
