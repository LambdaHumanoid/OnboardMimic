#!/usr/bin/env python3
"""Install missing Jetson TensorRT components without replacing an existing stack."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from onboardmimic.robots.unitree_g1.setup_support import require_idle, run


def target_profile(requested='auto'):
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    text = Path('/etc/nv_tegra_release').read_text()
    match = re.search(r'# R(\d+).*?REVISION:\s*([\d.]+)', text)
    l4t = '.'.join(match.groups()) if match else ''
    ubuntu = release.get('VERSION_ID', '').strip('"')
    if platform.machine() != 'aarch64' or release.get('ID', '').strip('"') != 'ubuntu':
        raise RuntimeError('Requires Ubuntu on ARM64 Jetson / 需要 ARM64 Jetson Ubuntu')
    if ubuntu == '20.04' and l4t.startswith('35.'):
        found, series = 'jp5', 'r35.'+l4t.split('.')[1]
    elif ubuntu == '22.04' and l4t.startswith('36.'):
        found, series = 'jp6', 'r36.'+l4t.split('.')[1]
    else:
        raise RuntimeError('Unsupported Ubuntu/L4T combination / 不支持此 Ubuntu/L4T 组合: '+ubuntu+'/'+l4t)
    if requested not in ('auto', found):
        raise RuntimeError('Installer does not match this JetPack / 安装入口与 JetPack 不匹配')
    return found, series


def compatible(version, profile):
    version = version.split(':')[-1]
    return bool(re.match(r'^8\.5\.2(?:[.-]|$)', version)) if profile == 'jp5' else version.startswith('10.')


def packages(profile, development):
    major = '8' if profile == 'jp5' else '10'
    names = ['libnvinfer'+major, 'libnvinfer-plugin'+major,
             'libnvonnxparsers'+major, 'python3-libnvinfer']
    if development:
        names += ['libnvinfer-dev', 'libnvinfer-plugin-dev', 'libnvonnxparsers-dev', 'libnvinfer-bin']
    return names


def installed_packages():
    result = subprocess.run(['dpkg-query', '-W', '-f=${binary:Package}\t${db:Status-Abbrev}\t${Version}\n',
                             'libnvinfer*', 'libnvonnxparsers*', 'libnvparsers*', 'python3-libnvinfer*'],
                            capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError('Cannot read installed packages / 无法读取已安装软件包: '+result.stderr)
    installed = {}
    for line in result.stdout.splitlines():
        name, status, version = line.split('\t')
        if status.startswith('ii'):
            installed[name.split(':')[0]] = version
        elif status.strip() not in ('un', 'rc'):
            raise RuntimeError('Incomplete dpkg state; repair before retrying / 软件包状态不完整，请先修复: '+name)
    return installed


def check_existing(installed, profile):
    wrong = {k:v for k,v in installed.items() if not compatible(v, profile)}
    if wrong:
        raise RuntimeError('Existing TensorRT version conflicts; nothing replaced / 已有 TensorRT 版本不匹配，未替换: '+str(wrong))
    versions = set(installed.values())
    if len(versions) > 1:
        raise RuntimeError('Mixed TensorRT package versions; nothing replaced / TensorRT 组件版本混用，未替换: '+str(installed))
    return next(iter(versions), None)


def available_versions(name, series):
    result = subprocess.run(['apt-cache', 'madison', name], capture_output=True, text=True, check=True)
    versions = set()
    for line in result.stdout.splitlines():
        fields = [part.strip() for part in line.split('|')]
        if len(fields) == 3 and re.search(r'https?://repo\.download\.nvidia\.com/jetson/common\s+'+re.escape(series)+r'/main\s+arm64\s+Packages', fields[2]):
            versions.add(fields[1])
    return versions


def choose_version(missing, installed_version, profile, available):
    candidates = set.intersection(*(set(available[name]) for name in missing))
    candidates = {v for v in candidates if compatible(v, profile)}
    if installed_version:
        if installed_version not in candidates:
            raise RuntimeError('Matching components for installed TensorRT are unavailable; no upgrade attempted / 软件源中没有与现有 TensorRT 完全匹配的缺失组件，未升级')
        return installed_version
    if not candidates:
        raise RuntimeError('Matching TensorRT packages not found in the NVIDIA Jetson apt source / 对应 NVIDIA Jetson 软件源中未找到匹配的 TensorRT 包，请检查网络和 /etc/apt/sources.list.d/nvidia-l4t-apt-source.list；不要改到其他 JetPack 源')
    best = next(iter(candidates))
    for version in sorted(candidates):
        if subprocess.run(['dpkg', '--compare-versions', version, 'gt', best]).returncode == 0:
            best = version
    return best


def inspect_plan(output):
    for line in output.splitlines():
        if line.startswith('Remv '):
            raise RuntimeError('Dependency plan would remove packages; stopped / 依赖计划包含卸载，已停止')
        match = re.match(r'Inst (\S+)(.*)', line)
        if match:
            name, rest = match.groups()
            if rest.lstrip().startswith('[') or name.startswith(('nvidia-l4t-', 'linux-', 'nvidia-driver-', 'cuda-drivers', 'cuda-compat')):
                raise RuntimeError('Dependency plan would change an installed package or platform driver; stopped / 依赖计划将更改已有软件包或平台驱动，已停止: '+line)


def verify(profile, expected=None):
    code = "import json,ctypes,tensorrt; ctypes.CDLL('libnvinfer.so.%s'); print(json.dumps({'tensorrt':tensorrt.__version__}))" % ('8' if profile == 'jp5' else '10')
    result = subprocess.run(['/usr/bin/python3', '-c', code], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('TensorRT library/Python verification failed / TensorRT 动态库或系统 Python 绑定检查失败: '+result.stderr)
    version = json.loads(result.stdout.strip().splitlines()[-1])['tensorrt']
    if not compatible(version, profile) or (expected and version.split('.')[:3] != expected.split(':')[-1].split('-')[0].split('.')[:3]):
        raise RuntimeError('System Python sees a different TensorRT; check Python path / 系统 Python 加载了不同版本的 TensorRT，请检查 Python 路径: '+version)
    print('TensorRT ready / TensorRT 就绪: '+version, flush=True)


def ensure_tensorrt(profile='auto', development=False, check=False):
    profile, series = target_profile(profile)
    installed = installed_packages()
    version = check_existing(installed, profile)
    missing = [name for name in packages(profile, development) if name not in installed]
    if not missing:
        verify(profile, version)
        return
    print('Missing TensorRT components / 缺少 TensorRT 组件: '+', '.join(missing), flush=True)
    if check:
        raise RuntimeError('Run the installer without --check to install these components / 不加 --check 运行安装命令即可自动补齐')
    require_idle()
    run(['sudo', 'apt-get', 'update'])
    available = {name:available_versions(name, series) for name in missing}
    selected = choose_version(missing, version, profile, available)
    args = [name+'='+selected for name in missing]
    options = ['--no-remove', '--no-upgrade']
    plan = subprocess.run(['apt-get', '-s', 'install', *options, *args], capture_output=True, text=True, check=True,
                          env=dict(os.environ, LC_ALL='C'))
    inspect_plan(plan.stdout)
    print('Install / 安装: '+' '.join(args), flush=True)
    run(['sudo', 'apt-get', 'install', '-y', *options, *args])
    after = installed_packages()
    check_existing(after, profile)
    if any(after.get(name) != selected for name in missing):
        raise RuntimeError('TensorRT packages did not reach the selected version / TensorRT 软件包安装结果不符合预期')
    verify(profile, selected)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('auto', 'jp5', 'jp6'), default='auto')
    parser.add_argument('--development', action='store_true', help='Include headers and trtexec / 同时补齐头文件和编译工具')
    parser.add_argument('--check', action='store_true', help='Read-only / 只检查')
    args = parser.parse_args(argv)
    ensure_tensorrt(args.profile, args.development, args.check)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit('TensorRT setup incomplete / TensorRT 安装未完成: '+str(exc))
