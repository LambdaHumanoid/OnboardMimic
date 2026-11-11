"""Download original models, or import a local deployment, verifying SHA-256."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from onboardmimic.core.paths import ROOT

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir',type=Path,help='Existing onboardmimic/backends/sonic/deploy directory; no network needed')
    args = parser.parse_args()
    spec = json.loads((ROOT/'onboardmimic/backends/sonic/resources.json').read_text())['models']
    for name, info in spec['files'].items():
        dest = ROOT/'onboardmimic/backends/sonic/deploy'/info['destination']
        if dest.exists():
            if digest(dest) != info['sha256']: raise SystemExit('Model hash mismatch: '+str(dest))
            print('Verified:',name); continue
        if args.models_dir:
            source = args.models_dir/info['destination']
        else:
            from huggingface_hub import hf_hub_download
            source = Path(hf_hub_download(repo_id=spec['repo'],filename=name,revision=spec['revision']))
        if digest(source) != info['sha256']: raise SystemExit('Model hash mismatch: '+str(source))
        dest.parent.mkdir(parents=True,exist_ok=True)
        tmp = dest.with_suffix('.partial')
        try:
            shutil.copyfile(source,tmp)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        print('Installed:',name)

if __name__ == '__main__': main()
