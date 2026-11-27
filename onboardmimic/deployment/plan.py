"""Conservative impact analysis over complete Git-visible source manifests."""
import hashlib
import json
from pathlib import Path, PurePosixPath

STATIC = ('onboardmimic/console/static/', 'lab/src/onboardmimic_lab/static/')
SONIC = 'onboardmimic/backends/sonic/deploy/'
BRIDGE = 'onboardmimic/robots/unitree_g1/native_bridge/'
UNITS = {'runtime':'onboardmimic.service', 'console':'onboardmimic-console.service',
         'lab_console':'onboardmimic-lab-console.service', 'collector':'onboardmimic-lab-collector.service',
         'bridge':'onboardmimic-bridge.service','primary':'onboardmimic-state-primary.service',
         'standby':'onboardmimic-state-standby.service','relay':'onboardmimic-state-relay.service',
         'connect':'onboardmimic-console-connect.service'}


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def manifest(root):
    root=Path(root).resolve();m=json.loads((root/'source-manifest.json').read_text())
    if m.get('schema')!=1:raise ValueError('Unsupported source manifest')
    sid=hashlib.sha256(json.dumps(m['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if sid!=m['source_id']:raise ValueError('Source manifest identity mismatch')
    for name,entry in m['files'].items():
        p=PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or not p.parts:raise ValueError('Unsafe manifest path')
        target=root/name
        if target.is_symlink() or root not in target.resolve().parents:raise ValueError('Source symlink escape')
        if digest(target)!=entry['sha256'] or bool(target.stat().st_mode&0o111)!=entry['executable']:
            raise ValueError('Source mismatch: '+name)
    return m


def impact(before, after):
    changed=sorted(n for n in before['files'].keys()|after['files'].keys()
                   if before['files'].get(n)!=after['files'].get(n))
    services=set();native=set();unknown=[];static=False
    for n in changed:
        if n.startswith(STATIC):static=True
        elif n.startswith(('docs/','lab/docs/','tests/','lab/tests/','.github/')) or n in ('README.md',):pass
        elif n.startswith(SONIC):
            native.update(('sonic','bridge'));services.update(('runtime','bridge','primary','standby'))
        elif n.startswith(BRIDGE):
            native.add('bridge');services.update(('runtime','bridge','primary','standby'))
        elif n=='onboardmimic/backends/sonic/native_source_manifest.json':
            native.update(('sonic','bridge'));services.update(('runtime','bridge','primary','standby'))
        elif n.startswith('onboardmimic/console/'):
            services.add('console')
            if n=='onboardmimic/console/assets.py':services.add('lab_console')
        elif n in ('lab/src/onboardmimic_lab/console.py','lab/src/onboardmimic_lab/replay_console.py','lab/src/onboardmimic_lab/native_replay_client.py','lab/src/onboardmimic_lab/release_assets.py'):
            services.add('lab_console')
        elif n.startswith('lab/src/onboardmimic_lab/'):
            services.update(('lab_console','collector'))
        elif n in ('onboardmimic/core/connection.py','onboardmimic/robots/unitree_g1/connection.py'):
            services.add('console') # presentation-only reader, used by Console
        elif n in ('onboardmimic/robots/unitree_g1/connect_services.py','onboardmimic/robots/unitree_g1/connect_services.sh'):
            services.add('connect')
        elif n.startswith(('onboardmimic/core/','onboardmimic/backends/','onboardmimic/robots/','onboardmimic/inputs/')):
            services.update(UNITS) # shared contracts/imports are not assumed independent
        elif n.startswith(('scripts/','onboardmimic/deployment/','configs/deployment/')):pass
        else:unknown.append(n)
    if unknown:services.update(UNITS)
    # Import-time VERSION, packaging and unknown changes need a conservative restart.
    if 'runtime' in services:services.update(('lab_console','collector','console'))
    return dict(source_id=after['source_id'],previous_source_id=before['source_id'],changed=changed,
                assets=static,services=sorted(services),native=sorted(native),unknown=unknown,
                requires_control_stopped=bool(services-{'console','lab_console','collector'}),
                requires_capture_idle=bool(services & {'collector','lab_console'}),
                requires_replay_idle=bool(services),
                mode='assets' if static and not services else 'services' if services else 'metadata')
