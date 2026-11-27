"""Compile isolated candidate native executors and compare measured-only rollouts."""
import argparse,ctypes,json,sys,shutil,subprocess
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from experiments.controlled_kneel.export_native_roll import export
from tests.simulation.protection_roll_probe import run

def candidate(task):
    name,config,out,cases=task;out=Path(out)/name;out.mkdir(parents=True,exist_ok=True)
    src=ROOT/'onboardmimic/robots/unitree_g1/native_bridge'
    for n in ['core.hpp','protection_roll.hpp','protection_kinematics.hpp']:shutil.copy2(src/n,out/n)
    export(config,out/'roll_parameters.hpp');(out/'config.json').write_text(json.dumps(config,indent=2))
    shim=ROOT/'tests/simulation/protection_roll_shim.cpp'
    shutil.copy2(shim,out/'shim.cpp')
    so=out/'executor.so';subprocess.run(['g++','-std=c++17','-shared','-fPIC','-O2','-I'+str(out),str(out/'shim.cpp'),'-o',str(so)],check=True)
    lib=ctypes.CDLL(str(so));ptr=np.ctypeslib.ndpointer(dtype=np.float32,flags='C_CONTIGUOUS')
    lib.protection_create.restype=ctypes.c_void_p;lib.protection_tune.argtypes=[ctypes.c_void_p,ctypes.c_double];lib.protection_destroy.argtypes=[ctypes.c_void_p]
    lib.protection_step.argtypes=[ctypes.c_void_p,ctypes.c_double,ctypes.c_double,ptr,ptr,ctypes.c_double,ctypes.c_double,ptr,ctypes.c_int,ptr]
    lib.protection_reason.argtypes=[ctypes.c_void_p];lib.protection_reason.restype=ctypes.c_char_p
    rows=[]
    for case in cases:
        r=run(lib,case,out/case['name']);geometry=json.loads((out/case['name']/'geometry.json').read_text())
        at=lambda t:min(geometry,key=lambda x:abs(x['time']-t))
        rows.append(dict(self_penetration_m=r['self_penetration_m'],candidate=name,case=case['name'],abort=r['abort'],butt_contact=r['butt_contact'],pelvis_peak_n=r['pelvis_peak_force_n'],torso_front_n=r['impact'].get('torso_front_ground',{}).get('peak_force_n',0),torso_back_n=r['impact'].get('torso_back_ground',{}).get('peak_force_n',0),geometry_05=at(.5),geometry_10=at(1.),final_z=r['final_z'],max_joint_speed=r['max_joint_speed']))
    return rows

def main():
    p=argparse.ArgumentParser();p.add_argument('--configs',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--cases',type=Path);a=p.parse_args()
    configs=json.loads(a.configs.read_text());cases=json.loads(a.cases.read_text()) if a.cases else [dict(name='nominal',vx=.1)]
    a.output.mkdir(parents=True,exist_ok=True);rows=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        for result in pool.map(candidate,[(name,c,str(a.output),cases) for name,c in configs.items()]):
            rows.extend(result);print(json.dumps(result),flush=True)
    (a.output/'comparison.json').write_text(json.dumps(rows,indent=2))
if __name__=='__main__':main()
