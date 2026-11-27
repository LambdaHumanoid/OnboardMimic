"""Persistent CMake workspace with content-preserving source synchronization."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from .plan import manifest, digest, SONIC, BRIDGE
from .activate import atomic


def mirror(source, target, files, prior):
    """Only owned files are removed. Unchanged files retain mtimes for CMake."""
    source,target=Path(source),Path(target)
    for name in set(prior)-set(files):
        path=target/name
        if path.exists() and path.is_file():path.unlink()
    for name,entry in files.items():
        expected=entry['sha256'];dest=target/name
        if dest.is_file() and not dest.is_symlink() and digest(dest)==expected:
            shutil.copymode(source/name,dest)
            continue
        if dest.is_symlink():raise ValueError('Cache source symlink: '+name)
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,dest)
        if digest(dest)!=expected:raise ValueError('Source changed during cache sync: '+name)
        # Changed content must invalidate objects even if a tar preserved old mtimes.
        os.utime(dest,None)


def build(candidate, cache, spec, components, runner=subprocess.run):
    candidate=Path(candidate).resolve();m=manifest(candidate)
    compiler=Path(spec['compiler']).resolve()
    version=runner([str(compiler),'--version'],capture_output=True,text=True,check=True).stdout
    deps={}
    for directory in spec.get('dependency_dirs',[]):
        root=candidate/directory
        if not root.is_dir():raise ValueError('Missing dependencies: '+directory)
        for p in root.rglob('*'):
            if p.is_file():deps[str(p.relative_to(candidate))]=dict(sha256=digest(p),executable=bool(p.stat().st_mode&0o111))
    identity=dict(compiler=digest(compiler),version=version,architecture=platform.machine(),spec=spec,
                  system_packages=digest('/var/lib/dpkg/status') if Path('/var/lib/dpkg/status').exists() else None,
                  dependencies=deps,environment={k:os.environ.get(k,'') for k in ('CMAKE_PREFIX_PATH','LD_LIBRARY_PATH','ROS_DISTRO')},
                  system_files={p:digest(p) for p in spec.get('system_files',[])})
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    workspace=Path(cache).resolve()/key;workspace.mkdir(parents=True,exist_ok=True)
    # Never point a live service at this mutable build workspace.
    with (workspace/'build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        source=workspace/'source';old=workspace/'inputs.json'
        prior=json.loads(old.read_text()) if old.exists() else {}
        files=dict(m['files']);files.update(deps)
        mirror(candidate,source,files,prior)
        atomic(old,json.dumps(files,sort_keys=True))
        atomic(workspace/'toolchain.json',json.dumps(identity,indent=2))
        binaries={};pending=set()
        previous=candidate/'native-build-verification.json'
        if previous.exists():
            prev=json.loads(previous.read_text())
            binaries=prev.get('binaries',{})
            pending=set(prev.get('pending_components',[]))
        for component in components:
            cfg=spec['components'][component];folder=workspace/('build-'+component)
            def expand(text):return str(text).format(source=source,build=folder,compiler=compiler)
            commands=[['cmake','-S',str(source/cfg['source']),'-B',str(folder),'-DCMAKE_CXX_COMPILER='+str(compiler)]+[expand(a) for a in cfg.get('configure',[])],
                      ['cmake','--build',str(folder),'--target',*cfg['targets'],'-j'+str(spec.get('jobs',2))]]
            commands += [[expand(a) for a in cmd] for cmd in cfg.get('tests',[])]
            for i,cmd in enumerate(commands):
                with (workspace/(component+'-'+str(i)+'.log')).open('w') as log:
                    runner(cmd,cwd=folder if i>=2 else workspace,stdout=log,stderr=subprocess.STDOUT,check=True)
            for relative,built in cfg['binaries'].items():
                origin=Path(expand(built));dest=candidate/relative
                dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(origin,dest)
                result=runner(['ldd',str(dest)],capture_output=True,text=True,check=True)
                if 'not found' in result.stdout:raise ValueError('Missing native dependency')
                binaries[relative]=dict(sha256=digest(dest),ldd=result.stdout)
        manifest(candidate)
        result=dict(source_id=m['source_id'],compiler=version.splitlines()[0],cache_key=key,
                    cache_workspace=str(workspace),built_components=list(components),binaries=binaries,
                    pending_components=sorted(pending-set(components)),
                    dependencies={n:v['sha256'] for n,v in deps.items()})
        atomic(candidate/'native-build-verification.json',json.dumps(result,indent=2))
        return result
