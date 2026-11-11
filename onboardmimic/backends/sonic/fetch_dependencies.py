"""Fetch versioned build dependencies; never invokes robot code."""
import json
from pathlib import Path
import subprocess
import shutil
import tarfile
import tempfile

from onboardmimic.core.paths import ROOT
DEPLOY = ROOT / 'onboardmimic/backends/sonic/deploy'
DEPS = DEPLOY / 'onboard/deps'

def main():
    spec = json.loads((ROOT/'onboardmimic/backends/sonic/resources.json').read_text())['unitree_sdk2']
    sdk = DEPLOY/'thirdparty/unitree_sdk2'
    if not sdk.exists():
        subprocess.run(['git','clone','--no-checkout',spec['url'],str(sdk)],check=True)
        subprocess.run(['git','-C',str(sdk),'checkout','--detach',spec['revision']],check=True)
    head = subprocess.check_output(['git','-C',str(sdk),'rev-parse','HEAD'],text=True).strip()
    if head != spec['revision']:
        raise SystemExit('SDK revision differs from resources.json; refusing to overwrite it')
    DEPS.mkdir(exist_ok=True)
    ort = 'onnxruntime-linux-aarch64-1.16.3'
    if not (DEPS/ort).exists():
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)/'ort.tgz'
            subprocess.run(['curl','--fail','--location','--retry','3','--connect-timeout','20','--max-time','300','-o',str(archive),'https://github.com/microsoft/onnxruntime/releases/download/v1.16.3/'+ort+'.tgz'],check=True)
            with tarfile.open(archive) as tf:
                # Allow only relative in-directory library symlinks; reject traversal/devices.
                for m in tf.getmembers():
                    if m.name.startswith('/') or '..' in Path(m.name).parts or not (m.isfile() or m.isdir() or m.issym()):
                        raise ValueError('Unexpected archive member: '+m.name)
                    if m.issym() and (m.linkname.startswith('/') or '..' in Path(m.linkname).parts):
                        raise ValueError('Unsafe archive link: '+m.name)
                staging=Path(tmp)/'unpack';staging.mkdir()
                tf.extractall(staging)
                shutil.move(str(staging/ort),str(DEPS/ort))
    cpp = DEPS/'cppzmq'; cpp.mkdir(exist_ok=True)
    for name in ('zmq.hpp','zmq_addon.hpp','LICENSE'):
        if not (cpp/name).exists():
            temp=cpp/(name+'.partial')
            subprocess.run(['curl','--fail','--location','--retry','3','--connect-timeout','20','--max-time','120','-o',str(temp),'https://raw.githubusercontent.com/zeromq/cppzmq/v4.10.0/'+name],check=True)
            temp.replace(cpp/name)
    print('Build dependencies ready; no controller was started. / 构建依赖已就绪；未启动控制器。')

if __name__ == '__main__': main()
