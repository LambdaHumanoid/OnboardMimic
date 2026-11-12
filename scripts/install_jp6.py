#!/usr/bin/env python3
"""JP6 source installation. Starts idle services only; never prepares or starts SONIC."""
import argparse
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from onboardmimic.backends.sonic.platforms import basic_environment, source_environment_errors
from onboardmimic.robots.unitree_g1.setup_support import require_idle, run


def check_platform(facts):
    if facts['arch'] != 'aarch64' or facts['ubuntu'] != '22.04' or not facts['l4t'].startswith('36.'):
        raise RuntimeError('Requires JetPack 6 / Ubuntu 22.04 / ARM64. JP5: use the released installer. / 需要 JP6、Ubuntu22.04、ARM64；JP5 请使用正式版安装入口。')


def install_steps(workspace, pico, upgrade):
    python = ROOT/'.venv/bin/python'
    env = dict(os.environ, PYTHON=str(python), ROS_SETUP='/opt/ros/humble/setup.bash',
               UNITREE_ROS_SETUP=str(workspace/'install/setup.bash'))
    steps = [(['bash', ROOT/'install_scripts/install_orin.sh'], None),
             (['bash', ROOT/'install_scripts/install_ros_dependencies.sh', '--workspace', workspace], None),
             (['bash', ROOT/'onboardmimic/backends/sonic/deploy/build_orin.sh'], env),
             (['bash', ROOT/'onboardmimic/robots/unitree_g1/native_bridge/build.sh'], env),
             (['bash', ROOT/'onboardmimic/backends/sonic/deploy/onboard/build_models.sh'], env)]
    if pico:
        steps.append(([python, '-m', 'pip', 'install', '-r', ROOT/'onboardmimic/backends/sonic/pico/requirements.txt'], env))
        if not shutil.which('docker'):
            steps.append((['sudo', 'apt-get', 'install', '-y', 'docker.io'], None))
    # Software checks are automatic; volunteers need not collect reports manually.
    steps.append(([python, ROOT/'scripts/validate_source.py', '--inference'], env))
    setup = [python, ROOT/'scripts/setup.py', '--source', '--configure-only', '--yes',
             '--access', 'lan', '--pico' if pico else '--no-pico']
    if upgrade:
        setup.append('--upgrade')
    steps.append((setup, env))
    return steps


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check versions only; no installation / 只检查版本')
    parser.add_argument('--no-pico', action='store_true', help='Planner only; omit PICO dependencies / 仅安装 Planner')
    parser.add_argument('--upgrade', action='store_true', help='Update stopped existing services / 更新已停止的现有服务')
    parser.add_argument('--workspace', type=Path, default=Path.home()/'.local/share/onboardmimic/ros2_jp6_ws')
    args = parser.parse_args(argv)
    facts = basic_environment()
    check_platform(facts)
    # Query the system Python's JetPack runtime before downloading/building anything.
    from onboardmimic.backends.sonic.source_validation import environment
    try:
        facts = environment()
    except (ImportError, OSError, RuntimeError) as exc:
        raise RuntimeError('JetPack CUDA/TensorRT runtime is incomplete. Install the matching JetPack SDK components first / 请先补齐当前 JetPack 对应的 CUDA/TensorRT 组件: '+str(exc)) from exc
    errors = source_environment_errors(facts)
    if errors:
        raise RuntimeError('; '.join(errors))
    print('Version check passed / 版本检查通过: Ubuntu '+facts['ubuntu']+', L4T '+facts['l4t']+', TensorRT '+facts['tensorrt'], flush=True)
    if args.check:
        return 0
    if os.geteuid() == 0:
        raise RuntimeError('Run as your normal robot user, without sudo / 请以普通用户运行，不要 sudo 整个脚本')
    require_idle()
    for command, env in install_steps(args.workspace.expanduser().absolute(), not args.no_pico, args.upgrade):
        run(command, cwd=ROOT, env=env)
    print('Installed. Open the console URL above to try Planner and PICO Pose. / 安装完成，请打开上方控制台地址测试 Planner 和 PICO Pose。')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit('Installation stopped / 安装未完成: '+str(exc))
