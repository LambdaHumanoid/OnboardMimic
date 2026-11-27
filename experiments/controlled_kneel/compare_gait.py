"""Replay frozen policy gait states through isolated native executors. No transport."""
import argparse
import ctypes
import json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tests.simulation.protection_roll_probe import run

def evaluate(task):
    name,library,case,output=task
    lib=ctypes.CDLL(library);ptr=np.ctypeslib.ndpointer(dtype=np.float32,flags='C_CONTIGUOUS')
    lib.protection_create.restype=ctypes.c_void_p
    lib.protection_tune.argtypes=[ctypes.c_void_p,ctypes.c_double]
    lib.protection_destroy.argtypes=[ctypes.c_void_p]
    lib.protection_step.argtypes=[ctypes.c_void_p,ctypes.c_double,ctypes.c_double,ptr,ptr,ctypes.c_double,ctypes.c_double,ptr,ctypes.c_int,ptr]
    lib.protection_reason.argtypes=[ctypes.c_void_p];lib.protection_reason.restype=ctypes.c_char_p
    r=run(lib,case,Path(output)/name/case['name'])
    r['candidate']=name
    return r

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--libraries',type=Path,required=True);p.add_argument('--cases',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();libraries=json.loads(a.libraries.read_text());cases=json.loads(a.cases.read_text());a.output.mkdir(parents=True,exist_ok=True)
    tasks=[(name,library,case,str(a.output)) for name,library in libraries.items() for case in cases]
    with ProcessPoolExecutor(max_workers=4) as pool:results=list(pool.map(evaluate,tasks))
    (a.output/'comparison.json').write_text(json.dumps(results,indent=2))
    for name in libraries:
        rows=[r for r in results if r['candidate']==name]
        print(json.dumps(dict(candidate=name,cases=len(rows),aborts=sum(r['abort'] is not None for r in rows),butt_contacts=sum(r['butt_contact'] is not None for r in rows),pelvis_peak_max=max(r['pelvis_peak_force_n'] for r in rows),torso_peak_max=max(r['impact'].get('torso_shell_ground',{}).get('peak_force_n',0) for r in rows))))

if __name__=='__main__':main()
