"""Source-build software checks. Never opens DDS or starts a controller/bridge."""
import argparse
import ctypes
import ctypes.util
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from onboardmimic.core.paths import ROOT
from onboardmimic.robots.unitree_g1.setup_support import require_idle, run
from .platforms import basic_environment, source_environment_errors

DEPLOY = Path('onboardmimic/backends/sonic/deploy')
ONBOARD = DEPLOY/'onboard'
REPORT = Path('var/source-validation.json')
BINARIES = [DEPLOY/'target/release/g1_deploy_onnx_ref'] + [
    Path('onboardmimic/robots/unitree_g1/native_bridge/build')/name
    for name in ('sonic_state_reader', 'sonic_command_bridge')]
RESOURCES = [DEPLOY/relative for relative in (
    'policy/release/model_encoder.onnx', 'policy/release/model_decoder.onnx',
    'planner/target_vel/V2/planner_sonic.onnx', 'planner/target_vel/V2/planner_orin_compatible.onnx',
    'policy/release/encoder_model_encoder.trt', 'policy/release/policy_model_decoder.trt',
    'planner/target_vel/V2/planner_planner_orin_compatible.trt')]
RESOURCES += [ONBOARD/'engines'/name for name in ('encoder_fp32.engine','decoder_fp32.engine','planner_fp32.engine')]


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


def environment():
    facts = basic_environment()
    import tensorrt
    facts['tensorrt'] = tensorrt.__version__
    cudart = ctypes.CDLL(os.environ.get('CUDART_LIBRARY') or ctypes.util.find_library('cudart') or '/usr/local/cuda/lib64/libcudart.so')
    version = ctypes.c_int()
    if cudart.cudaRuntimeGetVersion(ctypes.byref(version)) != 0:
        raise RuntimeError('CUDA runtime query failed')
    facts['cuda_runtime'] = version.value
    driver = ctypes.CDLL('libcuda.so.1')
    name = ctypes.create_string_buffer(256)
    if driver.cuInit(0) != 0 or driver.cuDeviceGetName(name, len(name), 0) != 0:
        raise RuntimeError('CUDA device query failed')
    facts['gpu'] = name.value.decode()
    return facts


def inventory(root=ROOT):
    # Source-checkout workflow. Include newly added files while developing locally.
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode().split('\0')
    names = sorted(set(n for n in names if n) | {str(p) for p in BINARIES+RESOURCES})
    result = {}
    for name in names:
        path = root/name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('Missing or symlinked source artifact: '+name)
        result[name] = digest(path)
    return result


def check_loaders(root, arch):
    loader, machine = ('/lib/ld-linux-aarch64.so.1', 183) if arch == 'aarch64' else ('/lib64/ld-linux-x86-64.so.2', 62)
    for relative in BINARIES:
        binary = root/relative
        with binary.open('rb') as stream:
            header = stream.read(20)
        if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != machine:
            raise RuntimeError('Wrong binary architecture: '+str(relative))
        command = [loader, '--list', str(binary)]
        if relative != BINARIES[0]:
            ros = os.environ.get('ROS_SETUP')
            workspace = os.environ.get('UNITREE_ROS_SETUP')
            if not ros or not workspace:
                raise RuntimeError('Set ROS_SETUP and UNITREE_ROS_SETUP for native bridge library checks')
            command = ['bash', '--noprofile', '--norc', '-c',
                       'source "$1" >/dev/null && source "$2" >/dev/null && exec "$3" --list "$4"',
                       'loader-check', ros, workspace, loader, str(binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode or 'not found' in result.stdout:
            raise RuntimeError('Unresolved native libraries: '+str(relative)+'\n'+result.stdout+result.stderr)


def check_timing(report):
    if report.get('errors') or not report.get('planner_present'):
        raise RuntimeError('Incomplete or failed inference benchmark')
    for name, budget in (('encoder_decoder', 20), ('planner', 100)):
        stats = report['results'][name]['completion_from_deadline']
        if (stats['n'] <= 0 or stats['over_budget'] != 0
                or not math.isfinite(stats['max_ms']) or stats['max_ms'] > budget):
            raise RuntimeError('Inference missed its cycle budget: '+name)


def verify_report(root=ROOT, facts=None):
    report = json.loads((root/REPORT).read_text())
    facts = environment() if facts is None else facts
    if source_environment_errors(facts):
        raise RuntimeError('Source setup requires the target Jetson configuration')
    if not report.get('software_passed') or report.get('host_test'):
        raise RuntimeError('Target software validation required; host tests cannot qualify a Jetson install')
    check_timing(report['numerical']['benchmark'])
    # Memory availability is variable, total installed memory is checked separately.
    for key in ('arch', 'ubuntu', 'l4t', 'tensorrt', 'cuda_runtime', 'gpu', 'python'):
        if report['environment'].get(key) != facts.get(key):
            raise RuntimeError('Environment changed; rerun source validation: '+key)
    if report.get('sha256') != inventory(root):
        raise RuntimeError('Source/build/model files changed; rerun source validation')
    check_loaders(root, facts['arch'])
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inference', action='store_true', help='Run numerical GPU checks and benchmark; no motor output')
    p.add_argument('--host-test', action='store_true', help='Non-Jetson diagnostic only; never qualifies target setup')
    p.add_argument('--benchmark-seconds', type=int, default=30)
    p.add_argument('--report', type=Path, default=ROOT/REPORT)
    a = p.parse_args(argv)
    if not 1 <= a.benchmark_seconds <= 3600:
        p.error('Benchmark duration must be 1..3600 seconds')
    if a.inference and not a.host_test and a.benchmark_seconds < 30:
        p.error('Target validation requires at least 30 seconds')
    report = {'software_passed': False, 'host_test': a.host_test, 'motor_output': False,
              'physical_acceptance': False, 'timestamp_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'checks': {}}
    status = 0
    try:
        facts = report['environment'] = environment()
        report['target_environment_errors'] = source_environment_errors(facts)
        if a.inference:
            require_idle()
            if report['target_environment_errors'] and not a.host_test:
                raise RuntimeError('; '.join(report['target_environment_errors']))
            before = inventory()
            check_loaders(ROOT, facts['arch'])
            report['checks']['native_loader'] = 'passed (entrypoints not executed)'
            commands = [('planner', ['validate_planner_engine.py', 'engines/planner_fp32.engine', 'planner_engine_validation.json', '--extended']),
                        ('policy', ['validate_engines.py']), ('benchmark', ['benchmark.py', str(a.benchmark_seconds)])]
            logs = ROOT/'var/source-check-logs'
            logs.mkdir(parents=True, exist_ok=True)
            for name, command in commands:
                with (logs/(name+'.log')).open('w') as output:
                    run([sys.executable, *command], cwd=ROOT/ONBOARD, stdout=output, stderr=subprocess.STDOUT)
                report['checks'][name] = 'passed'
            report['numerical'] = {name: json.loads((ROOT/ONBOARD/file).read_text()) for name, file in
                                  [('planner', 'planner_engine_validation.json'), ('policy', 'engine_validation.json'), ('benchmark', 'benchmark_result.json')]}
            check_timing(report['numerical']['benchmark'])
            if inventory() != before:
                raise RuntimeError('Source artifacts changed during validation')
            report['sha256'] = before
            report['software_passed'] = True
    except Exception as exc:
        report['error'] = str(exc)
        status = 1
    a.report.parent.mkdir(parents=True, exist_ok=True)
    temp = a.report.with_suffix('.tmp')
    temp.write_text(json.dumps(report, indent=2)+'\n')
    temp.replace(a.report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('sha256', 'numerical')}, indent=2))
    return status


if __name__ == '__main__':
    raise SystemExit(main())
