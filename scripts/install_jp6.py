#!/usr/bin/env python3
"""JP6 source/prebuilt installation; never prepares or starts SONIC."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from onboardmimic.backends.sonic.platforms import basic_environment, source_environment_errors
from scripts.install_tensorrt import ensure_tensorrt
from onboardmimic.robots.unitree_g1.setup_support import require_idle, run


def check_platform(facts):
    if facts['arch'] != 'aarch64' or facts['ubuntu'] != '22.04' or not facts['l4t'].startswith('36.'):
        raise RuntimeError('Requires JetPack 6 / Ubuntu 22.04 / ARM64. JP5: use the released installer. / 需要 JP6、Ubuntu22.04、ARM64；JP5 请使用正式版安装入口。')


def install_steps(workspace, pico, upgrade, prebuilt=None, lab=True, access='lan', origin=None):
    python = ROOT/'.venv/bin/python'
    env = dict(os.environ, PYTHON=str(python), ROS_SETUP='/opt/ros/humble/setup.bash',
               UNITREE_ROS_SETUP=str(workspace/'install/setup.bash'))
    steps = [(['bash', ROOT/'install_scripts/install_orin.sh'], None),
             (['bash', ROOT/'install_scripts/install_ros_dependencies.sh', '--workspace', workspace], None),
             (['bash', ROOT/'onboardmimic/backends/sonic/deploy/build_orin.sh'], env),
             (['bash', ROOT/'onboardmimic/robots/unitree_g1/native_bridge/build.sh'], env),
             (['bash', ROOT/'onboardmimic/backends/sonic/deploy/onboard/build_models.sh'], env)]
    if prebuilt is not None:
        steps = steps[:2]
        steps.append(([python, ROOT/'scripts/check_prebuilt.py'], env))
        if prebuilt.get('engine_policy') == 'build_on_target':
            steps.append((['bash', ROOT/'onboardmimic/backends/sonic/deploy/onboard/build_models.sh'], env))
        else:
            steps.append((['bash', '-c',
                           'set -e; cd "$1"; "$2" validate_engines.py; '
                           '"$2" validate_planner_engine.py engines/planner_fp32.engine planner_engine_validation.json --extended; '
                           'exec "$2" pack_native_cache.py',
                           'pack-cache', ROOT/'onboardmimic/backends/sonic/deploy/onboard', python], env))
    if pico:
        steps.append(([python, '-m', 'pip', 'install', '-r', ROOT/'onboardmimic/backends/sonic/pico/requirements.txt'], env))
        if not shutil.which('docker'):
            steps.append((['sudo', 'apt-get', 'install', '-y', 'docker.io'], None))
    # Software checks are automatic; volunteers need not collect reports manually.
    steps.append(([python, ROOT/'scripts/validate_source.py', '--inference'], env))
    setup = [python, ROOT/'scripts/setup.py', '--source', '--configure-only', '--yes',
             '--access', access, '--pico' if pico else '--no-pico', '--lab' if lab else '--no-lab']
    if origin:
        setup.extend(['--origin', origin])
    if upgrade:
        setup.append('--upgrade')
    steps.append((setup, env))
    return steps


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check versions only; no installation / 只检查版本')
    parser.add_argument('--no-pico', action='store_true', help='Planner only; omit PICO dependencies / 仅安装 Planner')
    parser.add_argument('--upgrade', action='store_true', help='Update stopped existing services / 更新已停止的现有服务')
    parser.add_argument('--no-lab', action='store_true', help='Install OM only / 仅安装 OM，默认同时安装 Lab')
    parser.add_argument('--access', choices=('local','lan'), default='lan')
    parser.add_argument('--origin', help='OM browser origin, e.g. http://ROBOT_IP:8769')
    parser.add_argument('--workspace', type=Path, default=Path.home()/'.local/share/onboardmimic/ros2_jp6_ws')
    args = parser.parse_args(argv)
    prebuilt = None
    manifest_path = ROOT/'prebuilt_manifest.json'
    if manifest_path.is_file():
        prebuilt = json.loads(manifest_path.read_text())
        if prebuilt.get('jetpack_profile') != 'jp6':
            raise RuntimeError('This package is not a JP6 runtime; download the JP6 package')
        from onboardmimic.backends.sonic.check_prebuilt import verify_files
        verify_files(ROOT, prebuilt)
    facts = basic_environment()
    check_platform(facts)
    if not args.check and os.geteuid() == 0:
        raise RuntimeError('Run as your normal robot user, without sudo / 请以普通用户运行，不要 sudo 整个脚本')
    # Bootstrap missing bindings/libraries before importing TensorRT.
    ensure_tensorrt(profile="jp6", development=True, check=args.check)
    # Query the system Python's JetPack runtime before model downloads/builds.
    from onboardmimic.backends.sonic.source_validation import environment
    try:
        facts = environment()
    except (ImportError, OSError, RuntimeError) as exc:
        raise RuntimeError('JetPack CUDA/TensorRT runtime is incomplete. Install the matching JetPack SDK components first / 请先补齐当前 JetPack 对应的 CUDA/TensorRT 组件: '+str(exc)) from exc
    errors = source_environment_errors(facts)
    if prebuilt is not None:
        from onboardmimic.backends.sonic.check_prebuilt import environment_errors
        errors = environment_errors(facts, prebuilt)
    if errors:
        raise RuntimeError('; '.join(errors))
    print('Version check passed / 版本检查通过: Ubuntu '+facts['ubuntu']+', L4T '+facts['l4t']+', TensorRT '+facts['tensorrt'], flush=True)
    if args.check:
        return 0
    require_idle()
    for command, env in install_steps(args.workspace.expanduser().absolute(), not args.no_pico, args.upgrade, prebuilt,
                                     not args.no_lab, args.access, args.origin):
        run(command, cwd=ROOT, env=env)
    print('Installed. Open the console URL above to try Planner and PICO Pose. / 安装完成，请打开上方控制台地址测试 Planner 和 PICO Pose。')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit('Installation stopped / 安装未完成: '+str(exc))
