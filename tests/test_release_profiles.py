"""Release packages must retain their target ABI and engine provenance."""
import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from onboardmimic.backends.sonic.release_profiles import release_platform
from onboardmimic.backends.sonic import source_validation
from tests import test_jetpack6


class ReleaseProfileTests(unittest.TestCase):
    def report(self):
        return {'sysroot': dict(ubuntu='22.04', l4t='36.4', cuda='12.6', tensorrt='10.3.0')}

    def test_cross_compiled_runtime_does_not_claim_target_inference(self):
        report = self.report()
        self.assertEqual(release_platform('jp6', report, report)['arch'], 'aarch64')
        with self.assertRaisesRegex(ValueError, 'target Jetson'):
            release_platform('jp6', report, report, {'software_passed': True, 'host_test': True})
        with self.assertRaises(ValueError):
            release_platform('jp5', report, report)
        with self.assertRaises(ValueError):
            release_platform('unknown', report, report)
        other = self.report(); other['sysroot']['cuda'] = '12.2'
        with self.assertRaisesRegex(ValueError, 'platforms differ'):
            release_platform('jp6', report, other)

    def test_prebuilt_install_keeps_inference_gate_without_recompiling_native(self):
        module = test_jetpack6.CommunityInstallerTests.module(self)
        steps = module.install_steps(Path('/tmp/ws'), False, False,
                                     {'engine_policy': 'build_on_target'})
        commands = [' '.join(map(str, c)) for c, _ in steps]
        self.assertFalse(any('build_orin.sh' in c or 'native_bridge/build.sh' in c for c in commands))
        self.assertTrue(any('build_models.sh' in c for c in commands))
        self.assertIn('--inference', commands[-2])
        self.assertIn('--configure-only', commands[-1])

    def test_extracted_package_inventory_needs_no_git_and_covers_generated_engines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in source_validation.BINARIES + source_validation.RESOURCES:
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'artifact')
            (root/'VERSION').write_text('0.2.0')
            manifest = root/'prebuilt_manifest.json'
            manifest.write_text(json.dumps({'files': {'VERSION': {}}}))
            with patch.object(source_validation.subprocess, 'check_output', side_effect=AssertionError('git forbidden')):
                before = source_validation.inventory(root)
                (root/source_validation.RESOURCES[-1]).write_bytes(b'changed engine')
                self.assertNotEqual(before, source_validation.inventory(root))
                manifest.write_text(json.dumps({'files': {'../outside': {}}}))
                with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                    source_validation.inventory(root)

    def test_jp6_runtime_rejects_other_tensor_rt_and_cuda_versions(self):
        from onboardmimic.backends.sonic.check_prebuilt import environment_errors
        manifest = {'jetpack_profile': 'jp6', 'platform': self.report()['sysroot']}
        facts = test_jetpack6.SourceValidationTests().facts()
        self.assertEqual(environment_errors(facts, manifest), [])
        for change in ({'tensorrt': '10.4.0'}, {'cuda_runtime': 12020}, {'l4t': '35.3.1'}):
            self.assertTrue(environment_errors(dict(facts, **change), manifest))

    def test_jp6_loader_check_uses_ros_aware_validation(self):
        from onboardmimic.backends.sonic import check_prebuilt as checker
        manifest = {'jetpack_profile': 'jp6', 'platform': self.report()['sysroot']}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'prebuilt_manifest.json').write_text(json.dumps(manifest))
            with patch.object(checker, 'ROOT', root), patch.object(checker, 'verify_files'), \
                    patch.object(checker, 'inspect_environment', return_value=test_jetpack6.SourceValidationTests().facts()), \
                    patch.object(source_validation, 'check_loaders') as loaders, \
                    patch('sys.argv', ['check-prebuilt']), contextlib.redirect_stdout(io.StringIO()):
                checker.main()
            loaders.assert_called_once_with(root, 'aarch64')
