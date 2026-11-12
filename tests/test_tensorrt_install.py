"""TensorRT bootstrap contracts; apt and GPU operations are mocked."""
import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from scripts import install_tensorrt as trt


class TensorRTInstallTests(unittest.TestCase):
    def installed(self, profile='jp5', development=False):
        version = '8.5.2-1+cuda11.4' if profile == 'jp5' else '10.3.0.30-1+cuda12.5'
        return {name:version for name in trt.packages(profile, development)}

    def test_complete_stack_never_invokes_apt(self):
        for profile in ('jp5','jp6'):
            with self.subTest(profile=profile), patch.object(trt,'target_profile',return_value=(profile,'r36.4')), patch.object(trt,'installed_packages',return_value=self.installed(profile)), patch.object(trt,'verify') as verify, patch.object(trt,'run') as run:
                trt.ensure_tensorrt(profile)
                run.assert_not_called();verify.assert_called_once_with(profile, next(iter(self.installed(profile).values())))

    def test_missing_stack_and_missing_python_are_installed_exactly(self):
        for profile,series in [('jp5','r35.3'),('jp6','r36.4')]:
            complete=self.installed(profile,True);version=next(iter(complete.values()))
            for before in ({},{k:v for k,v in complete.items() if k!='python3-libnvinfer'}):
                with self.subTest(profile=profile,before=before), patch.object(trt,'target_profile',return_value=(profile,series)), patch.object(trt,'installed_packages',side_effect=[before,complete]), patch.object(trt,'require_idle'), patch.object(trt,'run') as run, patch.object(trt,'available_versions',return_value={version}), patch.object(trt,'verify') as verify, patch.object(trt.subprocess,'run',return_value=SimpleNamespace(stdout='Inst python3-libnvinfer (test)\n',returncode=0)), contextlib.redirect_stdout(io.StringIO()):
                    trt.ensure_tensorrt(profile,development=True)
                    command=run.call_args_list[-1].args[0]
                    specs=[item for item in command if '=' in item]
                    self.assertEqual(set(specs),{name+'='+version for name in complete if name not in before})
                    self.assertIn('--no-remove',command);self.assertIn('--no-upgrade',command)
                    verify.assert_called_once_with(profile, version)

    def test_check_missing_is_read_only(self):
        with patch.object(trt,'target_profile',return_value=('jp6','r36.4')), patch.object(trt,'installed_packages',return_value={}), patch.object(trt,'run') as run, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'without --check'):trt.ensure_tensorrt(check=True)
            run.assert_not_called()

    def test_conflict_and_mixed_versions_never_mutate(self):
        for installed in ({'libnvinfer8':'8.6.2-1'},{'libnvinfer10':'10.3.0-1'},{'libnvinfer8':'8.5.2-1','python3-libnvinfer':'8.5.2-2'}):
            with self.subTest(installed=installed), patch.object(trt,'target_profile',return_value=('jp5','r35.3')), patch.object(trt,'installed_packages',return_value=installed), patch.object(trt,'run') as run:
                with self.assertRaises(RuntimeError):trt.ensure_tensorrt()
                run.assert_not_called()

    def test_running_controller_blocks_install(self):
        with patch.object(trt,'target_profile',return_value=('jp6','r36.4')), patch.object(trt,'installed_packages',return_value={}), patch.object(trt,'require_idle',side_effect=RuntimeError('Controller running')), patch.object(trt,'run') as run, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'Controller running'):trt.ensure_tensorrt()
            run.assert_not_called()

    def test_missing_matching_version_does_not_upgrade(self):
        with self.assertRaisesRegex(RuntimeError,'no upgrade'):
            trt.choose_version(['python3-libnvinfer'],'10.3.0-1','jp6',{'python3-libnvinfer':{'10.4.0-1'}})
        with self.assertRaisesRegex(RuntimeError,'not found'):
            trt.choose_version(['libnvinfer8'],None,'jp5',{'libnvinfer8':{'8.6.2-1'}})

    def test_sources_are_jetson_and_matching_release_only(self):
        output='''libnvinfer10 | 10.3.0-1 | https://repo.download.nvidia.com/jetson/common r36.4/main arm64 Packages
libnvinfer10 | 10.9.0-1 | https://repo.download.nvidia.com/jetson/common r36.5/main arm64 Packages
libnvinfer10 | 10.8.0-1 | https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/sbsa Packages
libnvinfer10 | 10.7.0-1 | https://example.com/jetson/common r36.4/main arm64 Packages'''
        with patch.object(trt.subprocess,'run',return_value=SimpleNamespace(stdout=output)):
            self.assertEqual(trt.available_versions('libnvinfer10','r36.4'),{'10.3.0-1'})

    def test_dependency_plan_protects_existing_packages_and_driver(self):
        trt.inspect_plan('Inst libnvinfer10 (10.3.0 repo [arm64])\nConf libnvinfer10 (10.3.0)')
        for line in ('Remv libnvinfer8 [8.5.2]','Inst libnvinfer10 [10.3.0] (10.4.0 repo [arm64])','Inst nvidia-l4t-core (36.4 repo [arm64])','Inst cuda-drivers (1 repo [arm64])'):
            with self.subTest(line=line),self.assertRaises(RuntimeError):trt.inspect_plan(line)

    def test_profile_is_selected_without_tensorrt(self):
        for ubuntu,l4t,expected in [('20.04','# R35 (release), REVISION: 3.1','jp5'),('22.04','# R36 (release), REVISION: 4.7','jp6')]:
            def read(path):
                return 'ID=ubuntu\nVERSION_ID="'+ubuntu+'"' if str(path)=='/etc/os-release' else l4t
            with patch.object(trt.Path,'read_text',read), patch.object(trt.platform,'machine',return_value='aarch64'):
                self.assertEqual(trt.target_profile()[0],expected)
                with self.assertRaises(RuntimeError):trt.target_profile('jp6' if expected=='jp5' else 'jp5')

    def test_python_binding_verification_does_not_start_controller(self):
        with patch.object(trt.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"tensorrt":"10.3.0"}',stderr='')) as run, contextlib.redirect_stdout(io.StringIO()):
            trt.verify('jp6')
            self.assertEqual(run.call_args.args[0][:2],['/usr/bin/python3','-c'])
            with self.assertRaises(RuntimeError):trt.verify('jp5')
            with self.assertRaises(RuntimeError):trt.verify('jp6','10.4.0.1-1+cuda12.5')
