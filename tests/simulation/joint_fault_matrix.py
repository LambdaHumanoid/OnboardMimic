"""Offline native-writer/MuJoCo regression; no transport or motor SDK.

Representative physical cases complement exhaustive 29-joint C++ tests.
Front-shell contact is a chest-area proxy, not internal electronics loading.
"""
import argparse
import ctypes
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.simulation.protection_roll_probe import run


def trial(args):
    library, case, output = args
    lib = ctypes.CDLL(str(library))
    ptr = np.ctypeslib.ndpointer(dtype=np.float32, flags='C_CONTIGUOUS')
    lib.protection_create.restype = ctypes.c_void_p
    lib.protection_tune.argtypes = [ctypes.c_void_p, ctypes.c_double]
    lib.protection_destroy.argtypes = [ctypes.c_void_p]
    lib.protection_step.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_double,
                                    ptr, ptr, ctypes.c_double, ctypes.c_double, ptr, ctypes.c_int, ptr]
    lib.protection_reason.argtypes = [ctypes.c_void_p]
    lib.protection_reason.restype = ctypes.c_char_p
    return run(lib, case, Path(output)/case['name'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', default='a25b945')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    # Refuse execution in a network namespace with any non-loopback interface.
    assert {p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}
    assert 1 <= args.workers <= 4
    args.output.mkdir(parents=True, exist_ok=False)
    native = ROOT/'onboardmimic/robots/unitree_g1/native_bridge'
    baseline = args.output/'baseline_source'
    baseline.mkdir()
    for path in native.glob('*.hpp'):
        (baseline/path.name).write_bytes(path.read_bytes())
    relative = str((native/'protection_roll.hpp').relative_to(ROOT))
    (baseline/'protection_roll.hpp').write_bytes(subprocess.check_output(
        ['git', 'show', args.baseline+':'+relative], cwd=ROOT))
    libraries = {}
    for name, include in [('candidate', native), ('baseline', baseline)]:
        so = args.output/(name+'.so')
        command = ['g++', '-std=c++17', '-shared', '-fPIC', '-O2', '-I'+str(include)]
        if name == 'baseline': command += ['-DOM_FAULT_BASELINE=1']
        subprocess.run(command+[str(ROOT/'tests/simulation/protection_roll_shim.cpp'), '-o', str(so)], check=True)
        libraries[name] = so
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in native.glob('*.hpp')}
    (args.output/'source-before.json').write_text(json.dumps(hashes, indent=2))
    cases = []
    measured = ROOT/'tests/simulation/fixtures/fault_measured_pose.npz'
    for pose in ('nominal', 'measured'):
        for kind in (1, 2):
            for joint in (0, 1, 6, 23):
                cases.append(dict(name=f'{pose}_kind{kind}_joint{joint:02}', failed_joints=[joint],
                    fault_kind=kind, vx=0., exit_at=.02,
                    **({'checkpoint': str(measured)} if pose == 'measured' else {})))
    cases += [dict(name='multi_feedback', failed_joints=[1, 7], fault_kind=2, vx=0., exit_at=.02),
              dict(name='fault_then_estop', failed_joints=[1], fault_kind=1, vx=0., exit_at=.02, cancel_at=.1)]
    jobs = [(libraries['candidate'], c, args.output) for c in cases]
    for joint in (0, 1, 6, 23):
        case = dict(name=f'baseline_measured_joint{joint:02}', failed_joints=[joint],
                    fault_kind=2, old_fault_baseline=True, vx=0., exit_at=.02, checkpoint=str(measured))
        jobs.append((libraries['baseline'], case, args.output))
    results = []
    with ProcessPoolExecutor(args.workers) as pool:
        for result in pool.map(trial, jobs):
            results.append(result)
            print(json.dumps(dict(case=result['case']['name'], abort=result['abort'],
                                  max_speed=result['max_joint_speed'], impact=result['impact'])), flush=True)
    import mujoco
    (args.output/'summary.json').write_text(json.dumps(dict(results=results, source_before=hashes,
        mujoco=mujoco.__version__, hardware_ready=False), indent=2))


if __name__ == '__main__': main()
