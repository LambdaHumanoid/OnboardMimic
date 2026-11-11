#!/usr/bin/env python3
"""Bilingual prebuilt setup wizard. Starts only the idle API/UI and optional receiver."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import time
import urllib.request

from scripts.install_services import ROOT, BOOT_UNITS, install, quote, render_units, require_stopped, write_atomic
from onboardmimic.robots.unitree_g1.setup_support import env_value, find_ros, lan_addresses, require_idle, run

from onboardmimic.robots.unitree_g1.bridge import network


def choose(prompt, choices, default):
    while True:
        answer = input(prompt+' ['+'/'.join(choices)+'] ('+default+'): ').strip().lower() or default
        if answer in choices:
            return answer


def access_dropin(root, config, access):
    base = render_units(root, config)['onboardmimic-console.service']
    command = next(line for line in base.splitlines() if line.startswith('ExecStart='))
    if access == 'lan':
        command = command.replace('--host 127.0.0.1', '--host 0.0.0.0')
    return '[Service]\nExecStart=\n'+command+'\n'


def configure(config, ros, workspace, interface, access):
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = config/'bridge.env'
    # Preserve unrelated bridge options, and snapshot existing configuration.
    for path in (env, config/'cyclonedds.xml'):
        if path.exists():
            shutil.copy2(path, path.with_name(path.name+'.before-setup'))
    from onboardmimic.robots.unitree_g1.setup_support import read_env
    old = read_env(env)
    template = old.get('CYCLONEDDS_URI', '') or ROOT/'onboardmimic/robots/unitree_g1/systemd/cyclonedds.xml.example'
    if str(template).startswith('file:///path/to/'):
        template = ROOT/'onboardmimic/robots/unitree_g1/systemd/cyclonedds.xml.example'
    network.configure(template, config/'cyclonedds.xml', interface)
    values = {'ROS_SETUP': str(ros), 'UNITREE_ROS_SETUP': str(workspace),
              'CYCLONEDDS_URI': (config/'cyclonedds.xml').as_uri()}
    lines = env.read_text().splitlines() if env.exists() else []
    lines = [line for line in lines if line.partition('=')[0].strip() not in values]
    lines += [key+'='+env_value(value) for key, value in values.items()]
    write_atomic(env, '\n'.join(lines)+'\n', 0o600)
    target = Path.home()/'.config/systemd/user/onboardmimic-console.service.d/90-setup-access.conf'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy2(target, config/'console-access.before-setup')
    write_atomic(target, access_dropin(ROOT, config, access))
    run(['systemctl', '--user', 'daemon-reload'])


def prepare_pico(previous_root=None):
    # Use sudo only for Docker, avoiding a logout/group-membership prerequisite.
    import onboardmimic.inputs.pico.install_service as pico
    path = ROOT/'onboardmimic/inputs/pico'
    if not shutil.which('docker'):
        raise RuntimeError('Docker missing / 缺少 Docker，请执行 sudo apt-get install -y docker.io 后重试')
    run(['sudo', 'systemctl', 'start', 'docker'])
    run([sys.executable, path/'fetch_dependencies.py'])
    run(['sudo', 'docker', 'build', '-t', pico.IMAGE, str(path)])
    pico.preserve_devices(previous_root,path/'devices.json')
    if not (path/'devices.json').exists():
        shutil.copy2(path/'devices.example.json', path/'devices.json')
    run([ROOT/'.venv/bin/python', ROOT/'scripts/install_pico_service.py', '--sudo-docker'])


def show_urls(access):
    user = pwd.getpwuid(os.getuid()).pw_name
    addresses = lan_addresses()
    print('\nConsole ready / 控制台就绪')
    if access == 'lan':
        for name, ip in addresses:
            print('  '+name+'  Desktop / 电脑: http://'+ip+':8769/')
            print('  '+name+'  Phone / 手机:   http://'+ip+':8769/remote')
        if not addresses:
            print('No LAN address yet / 暂无局域网地址；联网后重新运行 --check 查看。')
    else:
        host = addresses[0][1] if addresses else 'ROBOT_IP'
        print('  Laptop terminal / 电脑终端: ssh -N -L 8769:127.0.0.1:8769 '+user+'@'+host)
        print('  Laptop browser / 电脑浏览器: http://127.0.0.1:8769/')
    print('Manual steps / 手动操作: Connect services / 连接服务 → Prepare posture / 准备姿态 → Start SONIC / 启动 SONIC')
    print('API/UI recover to idle after restart; SONIC is never auto-started. / 后台重启后保持空闲，不自动启动 SONIC。')


def wait_console(timeout=25):
    deadline = time.monotonic()+timeout
    last = ''
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8769/api/controller', timeout=2) as response:
                state = json.load(response)
            if state.get('state') == 'stopped' and not state.get('controller_running'):
                return
            last = 'Unexpected startup state / 非空闲启动状态: '+str(state.get('state'))
        except (OSError, ValueError) as exc:
            last = str(exc)
        time.sleep(1)
    raise RuntimeError('Console readiness failed / 控制台未就绪: '+last+
                       '\nInspect / 查看: journalctl --user -u onboardmimic -u onboardmimic-console -n 80')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Read-only discovery / 只读检查')
    mode.add_argument('--install-only', action='store_true', help='Install/validate package without service changes / 仅安装验证，不改服务')
    mode.add_argument('--configure-only', action='store_true', help='Configure an already validated package / 配置已验证安装包')
    parser.add_argument('--yes', action='store_true', help='Noninteractive; defaults to local access, no PICO / 非交互，默认仅本机访问、不启用 PICO')
    parser.add_argument('--access', choices=('local', 'lan'))
    pico = parser.add_mutually_exclusive_group()
    pico.add_argument('--pico', dest='pico', action='store_true')
    pico.add_argument('--no-pico', dest='pico', action='store_false')
    parser.set_defaults(pico=None)
    parser.add_argument('--ros-setup', type=Path)
    parser.add_argument('--unitree-ros-setup', type=Path)
    parser.add_argument('--upgrade', action='store_true', help='Migrate stopped services, preserving configuration / 迁移已停止的服务并保留配置')
    args = parser.parse_args(argv)
    config = Path.home()/'.config/onboardmimic'
    if args.check:
        ros, workspace = find_ros(config, args.ros_setup, args.unitree_ros_setup)
        print('ROS:', ros, '\nUnitree:', workspace)
        print('Robot network / 机器人网口:', network.detect())
        from onboardmimic.robots.unitree_g1.setup_support import active_controllers
        print('Controller PIDs / 控制进程 PID:', [pid for pid,_ in active_controllers()])
        for name, ip in lan_addresses():
            print('LAN / 局域网:', name, ip)
        return
    if os.geteuid() == 0:
        raise RuntimeError('Run as the robot user, without sudo / 请以机器人普通用户执行，不要 sudo 整个向导')
    if not (ROOT/'prebuilt_manifest.json').is_file():
        raise RuntimeError('Extract all three release archives first / 请先解压同一版本的三个预编译包；源码安装见 docs/SOURCE_BUILD.md')
    require_idle()
    # The lock prevents two installers touching the same user's services/config.
    lock_path = Path.home()/'.cache/onboardmimic-setup.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ros, workspace = find_ros(config, args.ros_setup, args.unitree_ros_setup)
        print('ROS:', ros, '\nUnitree:', workspace)
        interface = network.detect()
        print('Robot network / 机器人网口:', interface)
        if not args.install_only:
            require_stopped(render_units(ROOT, config))
            old_pico = (Path.home()/'.config/systemd/user/onboardmimic.service.d/60-pico.conf').exists()
            if args.pico is False and old_pico:
                raise RuntimeError('Existing PICO configuration preserved; use --pico / 已有 PICO 配置，请使用 --pico；禁用参见升级指南')
            if old_pico and args.pico is None:
                args.pico = True
        if not args.yes and not sys.stdin.isatty():
            raise RuntimeError('Use --yes with --access local|lan and --pico/--no-pico / 非交互执行请指定参数')
        access = args.access or ('local' if args.yes else choose('Access / 访问方式: local=SSH, lan=电脑+手机局域网', ('local', 'lan'), 'lan'))
        use_pico = args.pico if args.pico is not None else (False if args.yes else choose('PICO receiver / 配置 PICO 接收器', ('y', 'n'), 'n') == 'y')
        if use_pico and not args.install_only:
            state = subprocess.run(['systemctl', 'show', 'onboardmimic-pico-receiver.service', '--property=ActiveState', '--value'], capture_output=True, text=True)
            if state.returncode or state.stdout.strip() not in ('inactive', 'failed'):
                raise RuntimeError('Stop the existing receiver first / 请先停止原接收器: sudo systemctl stop onboardmimic-pico-receiver.service')
        if not args.configure_only:
            env = dict(os.environ, ROS_SETUP=str(ros), UNITREE_ROS_SETUP=str(workspace))
            command = ['bash', ROOT/'install_scripts/install_prebuilt.sh']
            if use_pico:
                command.append('--with-pico')
            run(command, cwd=str(ROOT), env=env)
        elif not (ROOT/'.venv/bin/python').is_file():
            raise RuntimeError('Install the package first / 请先完成安装和推理验证')
        if args.install_only:
            print('Installation validated; services untouched / 安装验证完成，未改动服务。')
            print('Next / 下一步: python3 scripts/setup.py --configure-only'+(' --pico' if use_pico else ''))
            return
        require_idle()
        previous_manifest = config/'installed_services.json'
        previous_root = (json.loads(previous_manifest.read_text()).get('root')
                         if previous_manifest.exists() else None)
        install(upgrade=args.upgrade)
        configure(config, ros, workspace, interface, access)
        if use_pico:
            prepare_pico(previous_root)
            run(['sudo', 'systemctl', 'enable', '--now', 'onboardmimic-pico-receiver.service'])
        run(['systemctl', '--user', 'start', *BOOT_UNITS])
        wait_console()
        show_urls(access)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit('Setup incomplete / 安装未完成: '+str(exc))
