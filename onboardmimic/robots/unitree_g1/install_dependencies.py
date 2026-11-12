"""Install Foxy/Humble prerequisites and build an isolated, pinned Unitree workspace."""
import argparse
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import urllib.request

from onboardmimic.core.host_platform import os_release
from onboardmimic.robots.unitree_g1.setup_support import require_idle, run, probe_ros

SOURCES = (
    ('unitree_ros2', 'https://github.com/unitreerobotics/unitree_ros2.git', '668d1ec5a05d1c38d3306bdca7d59f2ba3581a88'),
    ('cyclonedds', 'https://github.com/eclipse-cyclonedds/cyclonedds.git', '9995905bce6c4cf9f740d6438bbf7fcfd1c83dfd'),
    ('rmw_cyclonedds', 'https://github.com/ros2/rmw_cyclonedds.git', 'c12abc56983204f1d91f2d839d394528c7b29b42'),
)


def checkout(base, name, url, revision):
    path = base/name
    if not path.exists():
        run(['git', 'init', path])
        run(['git', '-C', path, 'remote', 'add', 'origin', url])
    if subprocess.check_output(['git', '-C', path, 'remote', 'get-url', 'origin'], text=True).strip() != url:
        raise RuntimeError('Existing source has a different origin: '+str(path))
    if subprocess.check_output(['git', '-C', path, 'status', '--porcelain'], text=True).strip():
        raise RuntimeError('Existing source has local changes; preserved: '+str(path))
    run(['git', '-C', path, 'fetch', '--depth', '1', 'origin', revision])
    run(['git', '-C', path, 'checkout', '--detach', 'FETCH_HEAD'])
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path.home()/'.local/share/onboardmimic/ros2_ws')
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError('Run without sudo / 请用机器人普通用户运行')
    release = os_release()
    if platform.machine() != 'aarch64' or release.get('VERSION_ID', '').strip('"') not in ('20.04', '22.04') or release.get('ID', '').strip('"') != 'ubuntu':
        raise RuntimeError('This dependency recipe requires Ubuntu 20.04/22.04 aarch64 / 需要 Ubuntu 20.04/22.04 ARM64')
    distro, codename = ('humble', 'jammy') if release['VERSION_ID'].strip('"') == '22.04' else ('foxy', 'focal')
    require_idle()
    workspace = args.workspace.expanduser().absolute()
    # Never overwrite an existing non-wizard workspace or system ROS overlay.
    marker = workspace/'.onboardmimic-dependencies'
    if workspace.exists() and any(workspace.iterdir()) and not marker.exists():
        raise RuntimeError('Existing workspace preserved; choose an empty --workspace / 已保留原工作空间，请选择新目录')
    if marker.exists():
        previous = marker.read_text()
        previous_distro = 'humble' if 'ROS_DISTRO=humble' in previous else 'foxy'
        if previous_distro != distro:
            raise RuntimeError('ROS distribution changed; choose a new --workspace / ROS 版本变化，请使用新工作空间')
    workspace.mkdir(parents=True, exist_ok=True)
    marker.write_text('ROS_DISTRO='+distro+'\nPinned Unitree ROS2 dependencies; no robot executable is built.\n')
    ros = Path('/opt/ros')/distro/'setup.bash'
    if not ros.exists():
        # Official ROS repository recipe; retain an existing ROS apt source.
        sources = [Path('/etc/apt/sources.list')]+list(Path('/etc/apt/sources.list.d').glob('*'))
        configured = any(p.is_file() and 'packages.ros.org/ros2/ubuntu' in p.read_text(errors='replace') for p in sources)
        if not configured:
            run(['sudo', 'apt-get', 'update'])
            run(['sudo', 'apt-get', 'install', '-y', 'ca-certificates', 'software-properties-common'])
            run(['sudo', 'add-apt-repository', '-y', 'universe'])
            with tempfile.TemporaryDirectory() as tmp:
                key = Path(tmp)/'ros.key'
                with urllib.request.urlopen('https://raw.githubusercontent.com/ros/rosdistro/master/ros.key', timeout=30) as stream:
                    key.write_bytes(stream.read())
                source = Path(tmp)/'ros2-onboardmimic.list'
                # Foxy's official apt endpoint uses HTTP; authenticity is
                # verified by apt using the ROS key fetched over HTTPS above.
                source.write_text('deb [arch=arm64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu '+codename+' main\n')
                run(['sudo', 'install', '-m', '0644', key, '/usr/share/keyrings/ros-archive-keyring.gpg'])
                run(['sudo', 'install', '-m', '0644', source, '/etc/apt/sources.list.d/ros2-onboardmimic.list'])
    run(['sudo', 'apt-get', 'update'])
    run(['sudo', 'apt-get', 'install', '-y', '--no-upgrade', 'ros-'+distro+'-ros-base', 'ros-'+distro+'-rmw-cyclonedds-cpp',
         'ros-'+distro+'-rosidl-generator-dds-idl', 'ros-'+distro+'-geometry-msgs', 'python3-colcon-common-extensions',
         'python3-venv', 'git', 'cmake', 'build-essential', 'libssl-dev', 'libyaml-cpp-dev'])
    source_root = workspace/'src'
    source_root.mkdir(exist_ok=True)
    # Humble provides CycloneDDS through apt; do not overlay Foxy's RMW.
    for spec in (SOURCES if distro == 'foxy' else SOURCES[:1]):
        checkout(source_root, *spec)
    # Cyclone 0.10.2 is built without a sourced ROS environment, as Unitree requires.
    env = {k: v for k, v in os.environ.items() if k in ('HOME', 'USER', 'LOGNAME', 'TERM', 'http_proxy', 'https_proxy', 'no_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY')}
    env.update(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', LANG='C.UTF-8', MAKEFLAGS='-j2')
    if distro == 'foxy':
        run(['colcon', 'build', '--base-paths', 'src/cyclonedds', '--executor', 'sequential', '--cmake-args', '-DBUILD_TESTING=OFF'], cwd=str(workspace), env=env)
    script = '''source /opt/ros/foxy/setup.bash && source install/setup.bash &&
exec colcon build --base-paths src/rmw_cyclonedds/rmw_cyclonedds_cpp \\
 src/unitree_ros2/cyclonedds_ws/src/unitree/unitree_hg \\
 src/unitree_ros2/cyclonedds_ws/src/unitree/unitree_api \\
 --executor sequential --cmake-args -DBUILD_TESTING=OFF'''
    if distro == 'humble':
        script = '''source /opt/ros/humble/setup.bash &&
exec colcon build --base-paths src/unitree_ros2/cyclonedds_ws/src/unitree/unitree_hg \
 src/unitree_ros2/cyclonedds_ws/src/unitree/unitree_api \
 --executor sequential --cmake-args -DBUILD_TESTING=OFF'''
    run(['bash', '--noprofile', '--norc', '-c', script], cwd=str(workspace), env=env)
    ok, message = probe_ros(ros, workspace/'install/setup.bash')
    if not ok:
        raise RuntimeError(message)
    print('Dependencies built; original workspaces untouched / 依赖构建完成，原工作空间未改动。')
    print('Next: finish source builds and validation before setup / 下一步：先完成源码构建与验证，再配置服务。')
    print('JP6: docs/JETPACK6.md; JP5: docs/SOURCE_BUILD.md / docs/SOURCE_BUILD_zh.md')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit('Dependency setup incomplete / 依赖安装未完成: '+str(exc))
