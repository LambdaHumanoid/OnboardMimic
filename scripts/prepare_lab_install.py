"""Prepare Lab/protected replay configuration; --apply installs on an idle target."""
import argparse
import json
from pathlib import Path
import shlex
import os
import subprocess
from urllib.parse import urlsplit

from scripts.install_services import render_units
from onboardmimic.core.service_units import quote, unit_path


LAB_UNITS = ('onboardmimic-lab-collector.service', 'onboardmimic-lab-console.service')


def bundle(root, home, origin, pico=False, access='lan'):
    root, home = Path(root), Path(home)
    for p in (root, home): unit_path(p)
    parsed = urlsplit(origin)
    if any(ord(c)<33 for c in origin) or parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('Specify the exact OM browser origin')
    origin = origin.rstrip('/')
    if access not in ('local', 'lan'):
        raise ValueError('Choose local or lan access')
    host = '['+parsed.hostname+']' if ':' in parsed.hostname else parsed.hostname
    lab_url = parsed.scheme+'://'+host+':8773/embed'
    config = home/'.config/onboardmimic'
    data = home/'.local/share/onboardmimic-lab/data'
    socket = config/'protection/control.sock'
    spec = config/'controller-spec.json'
    for endpoint in (socket, home/'.local/state/onboardmimic-lab/collector.sock'):
        if len(os.fsencode(endpoint)) > 107:
            raise ValueError('Unix socket path exceeds 107 bytes; use a shorter user home / 用户 home 过长，请使用较短路径: '+str(endpoint))
    units = render_units(root, config)
    runtime = units['onboardmimic.service']
    command = next(s for s in runtime.splitlines() if s.startswith('ExecStart='))
    extra = ' --experimental-protection-socket '+quote(socket)+' --enable-reference-replay'
    if pico: extra += ' --pose-status '+quote(root/'onboardmimic/inputs/pico/runs/live/status.json')
    runtime = runtime.replace(command, command+extra)
    runtime = runtime.replace('[Service]', '[Service]\nUMask=0077\nEnvironment=OM_SONIC_NATIVE_RECEIVER=1\nEnvironment=OM_SONIC_NATIVE_TELEMETRY_PORT=16689\nEnvironment=OM_SONIC_BRIDGE_TELEMETRY_PORT=16690\nEnvironment=OM_SONIC_CONSUMED_TELEMETRY_PORT=16691\nEnvironment='+quote('OM_SONIC_SPEC_EXPORT='+str(spec)))
    # Generated drop-ins are applied only after reviewing existing customizations.
    result = {'units/onboardmimic.service.d/90-lab.conf': '[Service]\nExecStart=\n'+next(s for s in runtime.splitlines() if s.startswith('ExecStart='))+'\n'+ '\n'.join(s for s in runtime.splitlines() if s.startswith(('Environment=', 'UMask=')))+'\n'}
    result['units/onboardmimic-bridge.service.d/90-lab.conf'] = '[Service]\nEnvironment='+quote('ONBOARDMIMIC_EXPERIMENTAL_PROTECTION_SOCKET='+str(socket))+'\nEnvironment=OM_SONIC_BRIDGE_TELEMETRY_PORT=16690\n'
    result['units/onboardmimic-console.service.d/90-lab.conf'] = '[Service]\nEnvironment='+quote('ONBOARDMIMIC_LAB_EMBED_URL='+lab_url)+'\n'
    for role, module, args in (
        ('collector', 'collector_service', '--config '+quote(config/'collection.json')),
        ('console', 'console', '--host '+('127.0.0.1' if access=='local' else '0.0.0.0')+' --port 8773 --embed-origin '+quote(origin)+' --replay-root '+quote(data)+' --real-replay-config '+quote(config/'real-replay.json')),
    ):
        dependency = 'Wants=onboardmimic-lab-collector.service\nAfter=onboardmimic-lab-collector.service\n' if role=='console' else ''
        result['units/onboardmimic-lab-'+role+'.service'] = '[Unit]\nDescription=OnboardMimic Lab '+role+'\n'+dependency+'[Service]\nType=simple\nWorkingDirectory='+unit_path(root)+'\nExecStart='+quote(root/'.lab-venv/bin/python')+' -m onboardmimic_lab.'+module+' '+args+'\nKillMode=control-group\nRestart=on-failure\nRestartSec=3\nTimeoutStopSec=140\nUMask=0077\n[Install]\nWantedBy=default.target\n'
    connection = dict(gateway='http://127.0.0.1:18921', token_file=str(config/'http.token'))
    result['config/real-replay.json'] = json.dumps(connection, indent=2)+'\n'
    capture = dict(connection, capture_adapter='sonic-native-v2', root=str(data), same_host=True,
                   controller_spec_file=str(spec), telemetry_endpoint='tcp://127.0.0.1:16691', camera_port=18554)
    result['config/collection.json'] = json.dumps(capture, indent=2)+'\n'
    result['install-dependencies.sh'] = '#!/bin/bash\nset -euo pipefail\nsudo apt-get update\nsudo apt-get install -y python3-venv ffmpeg\n/usr/bin/python3 -m venv '+shlex.quote(str(root/'.lab-venv'))+'\n'+shlex.quote(str(root/'.lab-venv/bin/python'))+' -m pip install --upgrade pip==25.0.1\n'+shlex.quote(str(root/'.lab-venv/bin/python'))+' -m pip install '+shlex.quote(str(root)+'[capture]')+'\n'
    return result


def validate_bundle(root, home, files, require_token=True):
    """Preflight all destinations before installing dependencies or changing services."""
    from scripts.install_services import require_stopped
    from onboardmimic.robots.unitree_g1.setup_support import require_idle
    root, home = Path(root), Path(home)
    if os.geteuid() == 0 or home.resolve() != Path.home().resolve():
        raise ValueError('Apply as the target non-root user with their own home')
    require_idle()
    names = list(render_units(root, home/'.config/onboardmimic')) + ['onboardmimic-lab-collector.service', 'onboardmimic-lab-console.service']
    require_stopped(names)
    token = home/'.config/onboardmimic/http.token'
    if (require_token or token.exists()) and (not token.is_file() or token.stat().st_mode & 0o077 or len(token.read_text().strip()) < 32):
        raise ValueError('Install OM first; a private gateway token is required')
    # EnvironmentFile overrides unit Environment entries. Refuse a conflicting
    # existing bridge endpoint rather than configuring two different owners.
    env = home/'.config/onboardmimic/bridge.env'
    expected_env = {'ONBOARDMIMIC_EXPERIMENTAL_PROTECTION_SOCKET': str(home/'.config/onboardmimic/protection/control.sock'),
                    'OM_SONIC_BRIDGE_TELEMETRY_PORT': '16690'}
    if env.is_file():
        for line in env.read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() in expected_env and shlex.split(value) != [expected_env[key.strip()]]:
                raise ValueError('Existing bridge configuration preserved; reconcile '+key.strip())
    targets = {}
    roots = [root]
    manifest = home/'.config/onboardmimic/installed_services.json'
    if manifest.is_file():
        previous = json.loads(manifest.read_text()).get('root')
        if previous:
            roots.append(Path(previous))
    for name, text in files.items():
        if name.startswith('units/'):
            target = home/'.config/systemd/user'/name[len('units/'):]
        elif name.startswith('config/'):
            target = home/'.config/onboardmimic'/name[len('config/'):]
        else: continue
        if target.is_symlink() or (target.exists() and target.read_text() != text):
            raise ValueError('Existing configuration preserved; review migration: '+str(target))
        targets[target] = text
    for unit in ('onboardmimic.service', 'onboardmimic-console.service', 'onboardmimic-bridge.service', *LAB_UNITS):
        for existing in (home/'.config/systemd/user'/(unit+'.d')).glob('*.conf'):
            if unit == 'onboardmimic-console.service' and existing.name == '90-setup-access.conf':
                from onboardmimic.backends.sonic.setup import access_dropin
                if existing.read_text() in [access_dropin(r, home/'.config/onboardmimic', mode) for r in roots for mode in ('local','lan')]:
                    continue
            if unit == 'onboardmimic.service' and existing.name == '60-pico.conf':
                expected = []
                for r in roots:
                    base = render_units(r, home/'.config/onboardmimic')[unit]
                    command = next(line for line in base.splitlines() if line.startswith('ExecStart='))
                    expected.append(('[Service]\nExecStart=\n'+command+' --pose-status '+quote(r/'onboardmimic/inputs/pico/runs/live/status.json')).strip())
                if existing.read_text().strip() in expected and '--pose-status' in files['units/onboardmimic.service.d/90-lab.conf']:
                    continue
            if existing not in targets:
                raise ValueError('Custom drop-in preserved; merge before installation: '+str(existing))
    return targets


def write_bundle(output, files):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, text in files.items():
        path = output/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def install_dependencies(output):
    from onboardmimic.robots.unitree_g1.setup_support import require_idle
    from scripts.install_services import require_stopped
    require_idle()
    require_stopped(LAB_UNITS)
    subprocess.run(['bash', str(Path(output)/'install-dependencies.sh')], check=True)


def install_bundle(root, home, output, files, dependencies=True):
    from scripts.install_services import write_atomic
    root, home = Path(root), Path(home)
    validate_bundle(root, home, files)
    if dependencies:
        install_dependencies(output)
    targets = validate_bundle(root, home, files)
    private = home/'.config/onboardmimic/protection'
    if private.is_symlink(): raise ValueError('Protection directory must not be a symlink')
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    if private.stat().st_uid != os.getuid() or private.stat().st_mode & 0o077:
        raise ValueError('Protection directory must be owned by the user and mode 0700')
    for path, text in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, text, 0o600 if path.suffix=='.json' else 0o644)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','enable','onboardmimic-lab-console.service','onboardmimic-lab-collector.service'],check=True)
    print('Installed; services remain stopped. Start idle OM and Lab services after reviewing configuration.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--home',type=Path,required=True)
    p.add_argument('--origin',required=True)
    p.add_argument('--pico',action='store_true')
    p.add_argument('--access',choices=('local','lan'),default='lan')
    p.add_argument('--apply',action='store_true',help='Install on a stopped fresh target; preserve conflicting custom configuration')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    files=bundle(a.root,a.home,a.origin,a.pico,a.access)
    write_bundle(a.output,files)
    if a.apply: install_bundle(a.root,a.home,a.output,files)
    print('Bundle prepared; review units and existing drop-ins before installation:',a.output)

if __name__=='__main__':main()
