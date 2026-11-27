"""Package the complete Git-visible working tree, including untracked source.

Excludes ignored models, credentials, state, environments and build outputs.
No mutation, service operation or network access. Run from the checkout root.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import io

ROOT=Path(__file__).resolve().parents[1]

def snapshot():
    raw=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT)
    records={}
    for name in sorted(set(filter(None,raw.decode().split('\0')))):
        path=ROOT/name
        if not path.exists():continue
        if path.is_symlink():raise ValueError('Source symlink requires explicit handling: '+name)
        if not path.is_file():continue
        records[name]=dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),executable=bool(path.stat().st_mode&0o111))
    digest=hashlib.sha256(json.dumps(records,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return dict(schema=1,source_id=digest,files=records)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    manifest=snapshot()
    with tarfile.open(a.output,'w:gz') as archive:
        for name in manifest['files']:archive.add(ROOT/name,arcname=name,recursive=False)
        data=json.dumps(manifest,indent=2).encode();info=tarfile.TarInfo('source-manifest.json');info.size=len(data);archive.addfile(info,io.BytesIO(data))
    # Do not silently publish a mixed snapshot if files changed while archiving.
    with tarfile.open(a.output) as archive:
        for name,r in manifest['files'].items():
            if hashlib.sha256(archive.extractfile(name).read()).hexdigest()!=r['sha256']:
                raise RuntimeError('Source changed during packaging: '+name)
    print(json.dumps(dict(source_id=manifest['source_id'],files=len(manifest['files']),archive=str(a.output))))
