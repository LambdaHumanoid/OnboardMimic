#!/usr/bin/env python3
"""Read-only deployment inventory. Never imports SDKs or starts a controller.

This is a diagnostic snapshot, NOT an authorization token for motion.
Compatible with the robot's Python 3.8; standard library only.
"""
import argparse
import datetime
import json
import os
import re
from pathlib import Path
import subprocess

SERVICES = ('g1-teleop.service', 'g1-guide-scenes.service')
FILES = ('native_bridge/build/sonic_command_bridge',
         'native_bridge/build/sonic_state_reader', 'continuity/relay.py')
CONTROLLERS = {'g1_bridge', 'g1_bridge_node', 'start_g1_teleop.py', 'run_scene_console.sh',
               'g1_deploy', 'g1_deploy_onnx_ref', 'run_zmq_dds_bridge.py',
               'loopback_bridge.py', 'sonic_command_bridge'}


def control_executable(name):
    # Recognize command bridges across deployments without tying admission to
    # another application's name. DDS writer admission and locks remain required.
    return name in CONTROLLERS or name.endswith('_command_bridge')


def control_process(argv, _depth=0):
    """Recognize an executable/script entry, never an arbitrary data argument.

    This is an inventory heuristic, not a security boundary. Inline shell/Python
    code is not interpreted; a controller it launches is detected as its own
    process. Keep support for the gateway's owned_exec -> stdbuf -> controller.
    """
    if not argv or _depth > 4:
        return False
    program = Path(argv[0]).name
    if control_executable(program):
        return True
    if program == 'stdbuf':
        i = 1
        while i < len(argv):
            arg = argv[i]
            if arg == '--':
                i += 1
                break
            if arg in ('-i', '-o', '-e', '--input', '--output', '--error'):
                i += 2
            elif any(arg.startswith(x) for x in ('-i', '-o', '-e', '--input=', '--output=', '--error=')):
                i += 1
            else:
                break
        return control_process(argv[i:], _depth+1)
    python = re.fullmatch(r'python(?:[23](?:\.\d+)*)?', program) is not None
    shell = program in ('sh', 'bash', 'dash', 'zsh', 'ksh')
    if not python and not shell:
        return False
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == '--':
            i += 1
            break
        if python and arg in ('-h', '--help', '-V', '--version', '--help-env', '--help-xoptions', '--help-all'):
            return False
        if arg == '-' or (python and arg.startswith('-c')):
            return False
        if python and arg == '-m':
            # Only the module entry is a program, not its subsequent arguments.
            return i+1 < len(argv) and (argv[i+1] in {Path(x).stem for x in CONTROLLERS}
                                      or control_executable(argv[i+1]))
        if shell and (arg in ('--help', '--version', '--noexec') or
                      (arg.startswith('-') and not arg.startswith('--') and any(c in arg[1:] for c in 'csn')) or
                      (arg == '-o' and i+1 < len(argv) and argv[i+1] == 'noexec')):
            return False
        if arg in (('-W', '-X', '--check-hash-based-pycs') if python else ('-o', '+o')):
            i += 2
        elif arg.startswith('-'):
            i += 1
        else:
            break
    if i >= len(argv):
        return False
    script = Path(argv[i]).name
    if python and script == 'owned_exec.py':
        return (i+2 < len(argv) and argv[i+1].isdigit()
                and control_process(argv[i+2:], _depth+1))
    return control_executable(script)


def processes(proc=Path('/proc')):
    found, errors = [], []
    for directory in proc.iterdir():
        if not directory.name.isdigit():
            continue
        try:
            argv = [x.decode(errors='replace') for x in
                    (directory / 'cmdline').read_bytes().split(b'\0') if x]
            if control_process(argv):
                found.append({'pid': int(directory.name), 'argv': argv})
        except FileNotFoundError:
            pass  # A process can exit during inspection.
        except OSError as error:
            errors.append({'pid': int(directory.name), 'error': str(error)})
    return sorted(found, key=lambda p: p['pid']), errors


def service(name, user=False):
    try:
        result = subprocess.run(
            ['systemctl'] + (['--user'] if user else []) + ['show', name, '-p', 'LoadState', '-p', 'ActiveState',
             '-p', 'SubState', '-p', 'UnitFileState', '-p', 'MainPID'],
            capture_output=True, text=True, timeout=4)
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines()
                      if '=' in line)
        if result.returncode or not fields:
            return {'error': result.stderr.strip() or 'empty service status'}
        return fields
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'error': str(error)}


def assess(files, services, running, errors):
    blockers = []
    if any(not item['present'] for item in files.values()):
        blockers.append('deployment_files_missing')
    if running:
        blockers.append('existing_control_processes')
    if any(s.get('ActiveState') in ('active', 'activating', 'deactivating', 'reloading')
           for s in services.values()):
        blockers.append('existing_control_services')
    if errors or any('error' in s or s.get('LoadState') not in ('loaded', 'not-found')
                     for s in services.values()):
        blockers.append('inspection_incomplete')
    # Implementation exists, but physical commissioning is still pending.
    blockers.append('physical_commissioning_pending')
    return blockers


def inspect(root):
    files = {}
    for name in FILES:
        path = root / name
        files[name] = {'present': path.is_file() and path.stat().st_size > 0}
        if files[name]['present']:
            files[name]['bytes'] = path.stat().st_size
    units = {scope + ':external-motion-' + str(index+1): service(name, user=scope == 'user')
             for scope in ('system', 'user') for index,name in enumerate(SERVICES)}
    for value in units.values():
        if 'error' in value:value['error']='Service inspection failed'
    running, errors = processes()
    return {
        'schema_version': 1,
        'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'hostname': os.uname().nodename,
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'root': str(root), 'read_only': True, 'motion_enabled': False,
        'physical_takeover_ready': False,
        'files': files, 'services': units, 'control_processes': [{'pid':p['pid']} for p in running],
        'inspection_errors': errors,
        'blockers': assess(files, units, running, errors),
        'note': 'Snapshot only; file presence does not certify model integrity or motor safety.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path,
                        default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    print(json.dumps(inspect(args.root), ensure_ascii=False, indent=2))
