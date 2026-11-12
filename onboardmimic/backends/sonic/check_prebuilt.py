"""Check release checksums and compatible platform families. No DDS, controller or motor output."""
import argparse
import ctypes
import hashlib
import json
import platform
from pathlib import Path
import re
import subprocess

from onboardmimic.core.paths import ROOT

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def verify_files(root,manifest):
    for name,entry in manifest['files'].items():
        path=root/name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink():
            raise ValueError('Unsafe release path: '+name)
        if not path.is_file() or path.stat().st_size!=entry['size'] or sha256(path)!=entry['sha256']:
            raise ValueError('Missing or modified release file: '+name)

def environment_errors(facts):
    errors=[]
    for key,expected in [('arch','aarch64'),('ubuntu','20.04')]:
        if facts.get(key)!=expected:errors.append('%s: expected %r, got %r'%(key,expected,facts.get(key)))
    if not re.match(r'^35\.\d+(?:\.\d+)*$', facts.get('l4t','')):
        errors.append('JP5 / L4T 35.x required')
    if not 11040 <= facts.get('cuda_runtime',0) < 12000:
        errors.append('CUDA 11.4 or later CUDA 11.x runtime required')
    if not re.search(r'\borin\b', facts.get('gpu',''), re.I):
        errors.append('Orin GPU required')
    version=facts.get('tensorrt','').split(':')[-1]
    if not re.match(r'^8\.5\.2(?:[.-]|$)',version):errors.append('TensorRT 8.5.2 required for bundled engines')
    if facts.get('memory_kib',0)<12*1024*1024:errors.append('16 GB board required (8 GB is not supported)')
    return errors

def environment_notes(facts):
    notes=[]
    if facts.get('l4t')!='35.3.1' or facts.get('cuda_runtime')!=11040 or 'Orin NX' not in facts.get('board',''):
        notes.append('Compatible JP5 variant: continue with library and inference checks; this exact device configuration has not been qualified / JP5 相近配置，继续进行动态库与推理检查；此具体配置尚未完成真机验证')
    return notes

def inspect_environment():
    facts={'arch':platform.machine()}
    os_info=dict(line.split('=',1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    facts['ubuntu']=os_info.get('VERSION_ID','').strip('"') if os_info.get('ID','').strip('"')=='ubuntu' else ''
    release=Path('/etc/nv_tegra_release').read_text()
    match=re.search(r'# R(\d+).*?REVISION:\s*([\d.]+)',release)
    facts['l4t']='.'.join(match.groups()) if match else ''
    board=Path('/proc/device-tree/model')
    facts['board']=board.read_text().strip('\0\n') if board.exists() else ''
    memory=re.search(r'^MemTotal:\s+(\d+)',Path('/proc/meminfo').read_text(),re.M)
    facts['memory_kib']=int(memory[1]) if memory else 0
    facts['tensorrt']=subprocess.check_output(['dpkg-query','-W','-f=${Version}','libnvinfer8'],text=True).strip()
    cudart=ctypes.CDLL('libcudart.so.11.0');version=ctypes.c_int()
    if cudart.cudaRuntimeGetVersion(ctypes.byref(version))!=0:raise RuntimeError('Cannot query CUDA runtime')
    facts['cuda_runtime']=version.value
    cuda=ctypes.CDLL('libcuda.so.1');name=ctypes.create_string_buffer(256)
    if cuda.cuInit(0)!=0 or cuda.cuDeviceGetName(name,256,0)!=0:raise RuntimeError('Cannot query CUDA device')
    facts['gpu']=name.value.decode()
    return facts

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--files-only',action='store_true',help='Checksum audit on a non-Jetson host; not runtime qualification')
    args=p.parse_args()
    manifest=json.loads((ROOT/'prebuilt_manifest.json').read_text())
    verify_files(ROOT,manifest)
    print('All release file hashes match.')
    if args.files_only:return
    facts=inspect_environment();print(json.dumps(facts,indent=2))
    errors=environment_errors(facts)
    if errors:raise SystemExit('\n'.join(errors)+'\nUse the source build on an unsupported environment.')
    for note in environment_notes(facts):print(note)
    # Dynamic loader listing never executes the controller's main().
    binaries=['onboardmimic/backends/sonic/deploy/target/release/g1_deploy_onnx_ref',
              'onboardmimic/robots/unitree_g1/native_bridge/build/sonic_state_reader',
              'onboardmimic/robots/unitree_g1/native_bridge/build/sonic_command_bridge']
    for binary in binaries:
        result=subprocess.run(['/lib/ld-linux-aarch64.so.1','--list',str(ROOT/binary)],capture_output=True,text=True)
        if result.returncode or 'not found' in result.stdout:raise SystemExit('Runtime library resolution failed for '+binary+':\n'+result.stdout+result.stderr)
    print('Environment and runtime libraries accepted; inference validation is still required. No controller was executed.')

if __name__=='__main__':main()
