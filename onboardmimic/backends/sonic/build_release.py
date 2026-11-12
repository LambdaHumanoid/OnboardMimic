"""Maintainer: package an audited local snapshot; never executes ARM/robot programs."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from onboardmimic.core.paths import ROOT

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def verify_native_build(binary, build_report, source, source_hashes):
    if build_report.get('native_source_hashes')!=source_hashes:
        raise SystemExit('Build report source manifest differs from current native sources')
    if build_report.get('binary_sha256')!=digest(binary):
        raise SystemExit('Fresh native binary does not match its build report')
    if build_report.get('architecture')!='aarch64':raise SystemExit('Build must target aarch64')
    elf=binary.read_bytes()[:20]
    if elf[:4]!=b'\x7fELF' or elf[4:6]!=b'\x02\x01' or int.from_bytes(elf[18:20],'little')!=183:
        raise SystemExit('Expected a 64-bit little-endian AArch64 ELF binary')
    for name,expected in source_hashes.items():
        if digest(source/name)!=expected:
            raise SystemExit('Native source differs from rebuilt source: '+name)

def verify_bridge_build(directory, report, source):
    names=('sonic_state_reader','sonic_command_bridge')
    hashes={str(p.relative_to(source)):digest(p) for p in sorted(source.iterdir())
            if p.is_file() and (p.suffix in ('.cpp','.hpp') or p.name in ('CMakeLists.txt','build.sh'))}
    if report.get('source_hashes')!=hashes:raise SystemExit('Native bridge source report mismatch')
    for name in names:
        binary=directory/name
        metadata={'architecture':report.get('architecture'),'binary_sha256':report.get('binary_sha256',{}).get(name),'native_source_hashes':hashes}
        verify_native_build(binary,metadata,source,hashes)

def verify_startup_report(report):
    # --list resolves libraries but does not execute static constructors. A
    # newer cross-toolchain CRT with an older target libc can pass --list yet
    # skip C++ initialization. Require a pure, same-toolchain runtime probe.
    if report.get('validation',{}).get('cxx_static_initialization')!='passed':
        raise SystemExit('Missing target-sysroot C++ startup validation; rebuild and run tests/native_startup_smoke.cpp')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,required=True,help='Audited SONIC model/engine snapshot directory')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--patchelf',required=True)
    p.add_argument('--version',default='v0.1.1')
    p.add_argument('--native-binary',type=Path,required=True,help='Freshly built ARM64 controller; never executed by this script')
    p.add_argument('--build-report',type=Path,required=True,help='Source and binary hashes plus toolchain provenance')
    p.add_argument('--bridge-build',type=Path,required=True,help='Fresh ARM64 native bridge binaries')
    p.add_argument('--bridge-report',type=Path,required=True,help='Native bridge build/source hashes')
    a=p.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9._-]+',a.version):p.error('Invalid version')
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT):raise SystemExit('Commit or remove local changes before packaging')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    a.output.mkdir(parents=True,exist_ok=True)
    if any(a.output.iterdir()):raise SystemExit('Output directory must be empty')
    snap=a.snapshot.resolve()
    source_hashes=json.loads((ROOT/'onboardmimic/backends/sonic/native_source_manifest.json').read_text())
    build_report=json.loads(a.build_report.read_text())
    verify_startup_report(build_report)
    verify_native_build(a.native_binary,build_report,ROOT/'onboardmimic/backends/sonic/deploy',source_hashes)
    bridge_report=json.loads(a.bridge_report.read_text())
    verify_startup_report(bridge_report)
    verify_bridge_build(a.bridge_build,bridge_report,ROOT/'onboardmimic/robots/unitree_g1/native_bridge')
    model_spec=json.loads((ROOT/'onboardmimic/backends/sonic/resources.json').read_text())['models']
    for item in model_spec['files'].values():
        if digest(snap/'source'/item['destination'])!=item['sha256']:raise SystemExit('Original model hash mismatch')
    # Ensure bundled runtime libraries are the exact ones used for the new link.
    runtime_libraries={
        'libunitree_sdk2.a':snap/'source/thirdparty/unitree_sdk2/lib/aarch64/libunitree_sdk2.a',
        'libddsc.so':snap/'source/thirdparty/unitree_sdk2/thirdparty/lib/aarch64/libddsc.so',
        'libddscxx.so':snap/'source/thirdparty/unitree_sdk2/thirdparty/lib/aarch64/libddscxx.so',
        'libonnxruntime.so.1.16.3':snap/'deps/onnxruntime-linux-aarch64-1.16.3/lib/libonnxruntime.so.1.16.3',
    }
    if build_report.get('runtime_library_sha256')!={name:digest(path) for name,path in runtime_libraries.items()}:
        raise SystemExit('Runtime library snapshot differs from the native build report')
    planner_report=json.loads((snap/'planner_engine_validation_extended.json').read_text())
    if not planner_report.get('passed') or len(planner_report['cases'])!=24:raise SystemExit('Missing historical planner validation')
    with tempfile.TemporaryDirectory(prefix='onboardmimic-release-') as tmp:
        work=Path(tmp);stage=work/'OnboardMimic';stage.mkdir()
        # Export only tracked source files; never copy the snapshot wholesale.
        archive=work/'source.tar'
        subprocess.run(['git','archive','--format=tar','-o',str(archive),commit],cwd=ROOT,check=True)
        with tarfile.open(archive) as tf:tf.extractall(stage)
        groups={'runtime':set(str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()),'models':set(),'engines':set()}
        def copy(source,dest,group):
            target=stage/dest;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target);target.chmod(0o755 if source.stat().st_mode&0o111 else 0o644)
            groups[group].add(dest);return target
        binary_name='onboardmimic/backends/sonic/deploy/target/release/g1_deploy_onnx_ref'
        original=a.native_binary
        binary=copy(original,binary_name,'runtime')
        subprocess.run([a.patchelf,'--set-rpath','$ORIGIN/../lib:/usr/local/cuda-11.4/targets/aarch64-linux/lib',str(binary)],check=True)
        libbase='onboardmimic/backends/sonic/deploy/target/lib/'
        ort=snap/'deps/onnxruntime-linux-aarch64-1.16.3'
        copy(ort/'lib/libonnxruntime.so.1.16.3',libbase+'libonnxruntime.so.1.16.3','runtime')
        sdk=snap/'source/thirdparty/unitree_sdk2'
        for name in ['libddsc.so','libddscxx.so']:
            lib=copy(sdk/'thirdparty/lib/aarch64'/name,libbase+name+'.0','runtime')
            subprocess.run([a.patchelf,'--set-rpath','$ORIGIN',str(lib)],check=True)
        for name in ['LICENSE','ThirdPartyNotices.txt','VERSION_NUMBER']:
            copy(ort/name,'licenses/onnxruntime/'+name,'runtime')
        copy(sdk/'LICENSE','licenses/unitree_sdk2/LICENSE','runtime')
        for path in (sdk/'licenses').rglob('*'):
            if path.is_file():copy(path,'licenses/unitree_sdk2/'+str(path.relative_to(sdk/'licenses')),'runtime')
        for item in model_spec['files'].values():
            copy(snap/'source'/item['destination'],'onboardmimic/backends/sonic/deploy/'+item['destination'],'models')
        copy(snap/'source/planner/target_vel/V2/planner_orin_compatible.onnx','onboardmimic/backends/sonic/deploy/planner/target_vel/V2/planner_orin_compatible.onnx','models')
        copy(ROOT/'LICENSE','licenses/models/LICENSE','models')
        copy(a.build_report,'native_build_report.json','runtime')
        copy(a.bridge_report,'bridge_build_report.json','runtime')
        for name in ('sonic_state_reader','sonic_command_bridge'):
            copy(a.bridge_build/name,'onboardmimic/robots/unitree_g1/native_bridge/build/'+name,'runtime')
        for name in ['encoder_fp32.engine','decoder_fp32.engine','planner_fp32.engine']:
            copy(snap/'engines'/name,'onboardmimic/backends/sonic/deploy/onboard/engines/'+name,'engines')
        inventory={}
        for group,names in groups.items():
            for name in sorted(names):
                path=stage/name
                if path.suffix in ('.token','.key','.pem') or any(x in path.parts for x in ('private','__pycache__')):raise SystemExit('Forbidden file: '+name)
                inventory[name]={'sha256':digest(path),'size':path.stat().st_size,'asset_group':group}
        manifest={'profile':{'robot':'unitree_g1','backend':'sonic','layout':'onboardmimic'},'version':a.version,'source_commit':commit,'platform':{'board':'Jetson Orin NX 16GB','arch':'aarch64','ubuntu':'20.04','l4t':'35.3.1','cuda':'11.4','tensorrt':'8.5.2','gpu_cache_name':'Orin','precision':'FP32, TF32 disabled'},
                  'provenance':{'native_build':build_report.get('method','unspecified'),'build_timestamp_utc':build_report.get('built_at_utc'),'engine_snapshot':'2026-10-19 onboard deployment (unchanged engines)','original_binary_sha256':digest(original),'packaged_binary_sha256':digest(binary),'binary_modification':'Recompiled current native sources; RUNPATH relocated with patchelf','qualification':'Fresh ARM64 build with historical model/engine assets. Target-device inference and physical commissioning remain required; no robot execution during packaging.'},'files':inventory}
        (stage/'prebuilt_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        shutil.copyfile(stage/'prebuilt_manifest.json',a.output/'prebuilt_manifest.json')
        groups['runtime'].add('prebuilt_manifest.json')
        # Full-file audit before compression, without executing the controller.
        subprocess.run(['python3',str(stage/'scripts/check_prebuilt.py'),'--files-only'],check=True)
        assets=[]
        for group,names in groups.items():
            dest=a.output/('OnboardMimic-'+a.version+'-'+group+'.tar.gz')
            with dest.open('wb') as raw,gzip.GzipFile(filename='',mode='wb',fileobj=raw,compresslevel=1,mtime=0) as gz,tarfile.open(fileobj=gz,mode='w|') as tf:
                for name in sorted(names):
                    path=stage/name;info=tf.gettarinfo(str(path),arcname='OnboardMimic/'+name)
                    info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
                    with path.open('rb') as source:tf.addfile(info,source)
            if dest.stat().st_size>=2*1024**3:raise SystemExit('Asset exceeds 2 GiB: '+str(dest))
            assets.append(dest);print(group,dest.stat().st_size,flush=True)
        # Verify every compressed member against its manifest, including bytes and path safety.
        for asset in assets:
            with tarfile.open(asset,'r|gz') as tf:
                for member in tf:
                    if not member.isfile() or not member.name.startswith('OnboardMimic/') or '..' in Path(member.name).parts:raise ValueError('Unexpected archive member')
                    name=member.name[len('OnboardMimic/'):];h=hashlib.sha256()
                    with tf.extractfile(member) as stream:
                        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
                    expected=digest(stage/'prebuilt_manifest.json') if name=='prebuilt_manifest.json' else inventory[name]['sha256']
                    if h.hexdigest()!=expected:raise ValueError('Archive verification failed: '+name)
        assets.append(a.output/'prebuilt_manifest.json')
        (a.output/'SHA256SUMS').write_text(''.join(digest(path)+'  '+path.name+'\n' for path in assets))
        print('All archives verified. Source commit:',commit,flush=True)

if __name__=='__main__':main()
