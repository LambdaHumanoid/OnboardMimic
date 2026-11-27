"""Plan/stage/build/activate complete releases with minimal disruption.

Run via python -m scripts.deploy_incremental. No subcommand drives the robot.
"""
import argparse
import json
from pathlib import Path
from onboardmimic.deployment.plan import manifest, impact
from onboardmimic.deployment.stage import stage
from onboardmimic.deployment.activate import apply, request, guard
from onboardmimic.deployment.build import build


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for name in ('plan','stage','activate','build'):
        q=sub.add_parser(name);q.add_argument('--current',type=Path,required=True);q.add_argument('--candidate',type=Path,required=True)
        if name=='activate':q.add_argument('--config',type=Path)
        if name=='build':
            q.add_argument('--cache',type=Path,required=True);q.add_argument('--spec',type=Path,required=True)
            q.add_argument('--offline',action='store_true',help='Developer/cross build only; do not use on an active robot')
    a=p.parse_args()
    if a.command=='plan':result=impact(manifest(a.current),manifest(a.candidate))
    elif a.command=='stage':result=stage(a.current,a.candidate)
    elif a.command=='activate':result=apply(a.current,a.candidate,a.config)
    else:
        plan=impact(manifest(a.current),manifest(a.candidate))
        if not a.offline:
            token=(Path.home()/'.config/onboardmimic/http.token').read_text().strip()
            control=request('http://127.0.0.1:18921/api/controller',token)
            guard(dict(requires_control_stopped=True,requires_replay_idle=True,requires_capture_idle=False),control,
                  bridge_active=control.get('protection_executor',{}).get('configured',False) and control.get('protection_executor',{}).get('fresh',False))
        result=build(a.candidate,a.cache,json.loads(a.spec.read_text()),plan['native']) if plan['native'] else dict(native_build_required=False)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
