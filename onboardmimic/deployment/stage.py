"""Attach verified runtime assets to a complete immutable source snapshot."""
import json
from pathlib import Path
import shutil
from .plan import manifest, impact, digest, SONIC, BRIDGE
from .activate import atomic


def stage(current, candidate):
    current,candidate=Path(current).resolve(),Path(candidate).resolve()
    if current==candidate:raise ValueError('Never stage over the live release')
    before,after=manifest(current),manifest(candidate);plan=impact(before,after)
    for name in ('.venv','.lab-venv'):
        source=current/name;dest=candidate/name
        if not source.exists():raise ValueError('Missing runtime environment: '+name)
        if not dest.exists():dest.symlink_to(source.resolve())
        elif dest.resolve()!=source.resolve():raise ValueError('Unexpected runtime environment')
    dependencies={}
    for relative in (SONIC+'thirdparty',SONIC+'onboard/deps'):
        for source in (current/relative).rglob('*'):
            if source.is_file():
                name=str(source.relative_to(current));dest=candidate/name;sha=digest(source)
                # Git-visible dependency sources must match the candidate snapshot.
                if name in after['files']:continue
                dest.parent.mkdir(parents=True,exist_ok=True)
                if not dest.exists():shutil.copy2(source,dest)
                if digest(dest)!=sha:raise ValueError('Dependency mismatch: '+name)
                dependencies[name]=sha
    assets=json.loads((current/'device-artifacts.json').read_text())
    for name,row in assets['assets'].items():
        source=current/name
        if not source.exists():source=Path(row['source'])
        if digest(source)!=row['sha256']:raise ValueError('Model asset changed: '+name)
        dest=candidate/name;dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():shutil.copy2(source,dest)
        if digest(dest)!=row['sha256']:raise ValueError('Model staging mismatch')
    atomic(candidate/'device-artifacts.json',json.dumps(assets,indent=2))
    report=json.loads((current/'native-build-verification.json').read_text())
    if report['source_id']!=before['source_id']:raise ValueError('Previous native build identity mismatch')
    binaries={}
    for name,row in report['binaries'].items():
        if digest(current/name)!=row['sha256']:raise ValueError('Previous binary changed')
        component='sonic' if name.startswith(SONIC) else 'bridge'
        if component in plan['native']:continue
        dest=candidate/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(current/name,dest)
        binaries[name]=row
    result=dict(source_id=after['source_id'],compiler=report['compiler'],binaries=binaries,
                reused_from_source_id=before['source_id'],pending_components=plan['native'],dependencies=dependencies)
    atomic(candidate/'native-build-verification.json',json.dumps(result,indent=2))
    manifest(candidate)
    return dict(plan=plan,source_id=after['source_id'],stage_only=True)
