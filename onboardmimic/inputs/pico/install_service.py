"""Configure optional receive-only PICO services; never start or stop anything."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from scripts.install_services import ROOT, quote, require_stopped, render_units, write_atomic

from onboardmimic.core.registry import select, DEFAULT_ROBOT, DEFAULT_BACKEND

IMAGE = 'onboardmimic-pico-receiver:20261020'
NAME = 'onboardmimic-pico-receiver.service'


def render(root, config, robot=DEFAULT_ROBOT, backend=DEFAULT_BACKEND):
    body, controller = select(robot,backend)
    if "pico" not in controller.supported_inputs:
        raise ValueError("Selected backend does not support PICO")
    pico = root / 'onboardmimic/inputs/pico'
    if ',' in str(pico):
        raise ValueError('Docker bind paths cannot contain commas')
    mounts = ' '.join(quote('type=bind,src='+str(src)+',dst='+dst+suffix) for src, dst, suffix in (
        (pico/'devices.json', '/config/devices.json', ',readonly'),
        (pico/'runs/live', '/capture', ''),
        (pico/'receiver.py', '/app/receiver.py', ',readonly'),
        (pico/'container_capture.sh', '/app/container_capture.sh', ',readonly')))
    mounts = mounts.replace('" "', '" --mount "')
    unit = '''[Unit]
Description=OnboardMimic PICO receive-only tracking
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=/usr/bin/docker run --runtime runc --rm --init --name onboardmimic-pico-live --network host --cap-drop ALL --security-opt no-new-privileges --log-opt max-size=10m --log-opt max-file=2 --env PICO_CAPTURE_SECONDS=0 --env PICO_RECORD=0 --mount {mounts} {image} bash /app/container_capture.sh
ExecStop=/usr/bin/docker stop -t 5 onboardmimic-pico-live
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
[Install]
WantedBy=multi-user.target
'''.format(mounts=mounts, image=IMAGE)
    runtime = render_units(root, config, robot=robot, backend=backend)['onboardmimic.service']
    command = next(line for line in runtime.splitlines() if line.startswith('ExecStart='))
    dropin = '[Service]\nExecStart=\n'+command+' --pose-status '+quote(pico/'runs/live/status.json')+'\n'
    return unit, dropin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true', help='Print service definitions only / 只预览')
    parser.add_argument('--sudo-docker', action='store_true', help='Use sudo for image inspection / 使用 sudo 检查镜像，无需加入 docker 组')
    parser.add_argument("--robot",default=DEFAULT_ROBOT)
    parser.add_argument("--backend",default=DEFAULT_BACKEND)
    args = parser.parse_args()
    config = Path.home()/'.config/onboardmimic'
    unit, dropin = render(ROOT, config, args.robot, args.backend)
    if args.preview:
        print(unit+'\n# onboardmimic.service.d/60-pico.conf\n'+dropin)
        return
    if os.geteuid() == 0:
        raise SystemExit('Run as the robot user, without sudo / 请以机器人普通用户运行，不要 sudo 整个脚本')
    require_stopped(render_units(ROOT, config, robot=args.robot, backend=args.backend))
    state = subprocess.run(['systemctl', 'show', NAME, '--property=ActiveState', '--value'], capture_output=True, text=True)
    if state.returncode or state.stdout.strip() not in ('inactive', 'failed'):
        raise SystemExit('Stop the existing PICO service first / 请先停止现有 PICO 接收服务')
    units = Path.home()/'.config/systemd/user'
    baseline = render_units(ROOT, config, robot=args.robot, backend=args.backend)['onboardmimic.service']
    if (units/'onboardmimic.service').read_text() != baseline:
        raise SystemExit('Install standard services first; custom API units require manual configuration / 请先安装标准服务；自定义 API unit 请手动配置')
    pico = ROOT/'onboardmimic/inputs/pico'
    devices = pico/'devices.json'
    json.loads(devices.read_text())
    subprocess.run((['sudo'] if args.sudo_docker else [])+['docker', 'image', 'inspect', IMAGE], check=True, stdout=subprocess.DEVNULL)
    target = units/'onboardmimic.service.d/60-pico.conf'
    if target.exists() and target.read_text() != dropin:
        raise SystemExit('Existing custom PICO drop-in preserved / 已保留自定义 PICO drop-in，请手动迁移')
    # Root in a cap-drop ALL container cannot bypass host file permissions.
    subprocess.run(['sudo', 'install', '-d', '-o', 'root', '-g', 'root', '-m', '0755', str(pico/'runs/live')], check=True)
    subprocess.run(['sudo', 'chgrp', 'root', str(devices)], check=True)
    subprocess.run(['chmod', '0640', str(devices)], check=True)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp)/NAME
        source.write_text(unit)
        subprocess.run(['sudo', 'install', '-m', '0644', str(source), '/etc/systemd/system/'+NAME], check=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, dropin)
    subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    print('Configured only. No service started or enabled. / 仅完成配置，未启动或启用服务。')
    print('Receive tracking / 接收追踪: sudo systemctl enable --now '+NAME)
    print('Start idle API and console / 启动空闲后台和页面: systemctl --user start onboardmimic.service onboardmimic-console.service')


if __name__ == '__main__':
    main()


def preserve_devices(previous_root, destination):
    """Copy only existing device configuration, never captures, commands or credentials."""
    import shutil
    destination = Path(destination)
    if destination.exists() or previous_root is None:
        return
    source = Path(previous_root)/'onboardmimic/inputs/pico/devices.json'
    if source.is_file():
        json.loads(source.read_text())
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source,destination)
