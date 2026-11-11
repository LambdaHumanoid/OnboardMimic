"""Standard-library helpers for installation; no ROS nodes or control commands."""
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import time

from .preflight import CONTROLLERS, control_executable, control_process


def active_controllers(proc=Path('/proc')):
    active = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # Executable names, not a substring search of shell commands.
            comm = (entry/'comm').read_text().strip()
            try:
                name = (entry/'exe').resolve().name
            except (OSError, RuntimeError):
                name = comm  # /proc/PID/exe may be private for another user.
            # Script names live in cmdline, while comm truncates binary names.
            try:
                argv = [x.decode(errors='replace') for x in (entry/'cmdline').read_bytes().split(b'\0') if x]
            except OSError:
                argv = []
            if (control_executable(name) or control_process(argv)
                    or comm in {n[:15] for n in CONTROLLERS}):
                active.append((entry.name, name))
        except (OSError, RuntimeError):
            continue
    return active


def require_idle():
    active = active_controllers()
    if active:
        raise RuntimeError('Controller/bridge running; installation paused, nothing stopped / '
                           '检测到控制器或桥接，未停止它们，请先在现场停止后重试; PIDs: '+str([pid for pid,_ in active]))


def run(command, **kwargs):
    """Check before each stage and stop our stage if a controller appears."""
    require_idle()
    print('+ '+shlex.join(map(str, command)), flush=True)
    # Keep the controlling terminal: sudo must be able to prompt during setup.
    process = subprocess.Popen(list(map(str, command)), **kwargs)
    try:
        while process.poll() is None:
            require_idle()
            time.sleep(0.5)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
    except BaseException:
        if process.poll() is None:
            children = descendants(process.pid)
            for pid in reversed(children):
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise


def descendants(parent):
    """Find only this install stage's children; never signal another controller."""
    mapping = {}
    for path in Path('/proc').glob('[0-9]*/status'):
        try:
            values = dict(line.split(':', 1) for line in path.read_text().splitlines() if ':' in line)
            mapping.setdefault(int(values['PPid']), []).append(int(path.parent.name))
        except (OSError, ValueError, KeyError):
            continue
    children = []
    pending = [parent]
    while pending:
        found = mapping.get(pending.pop(), [])
        children.extend(found)
        pending.extend(found)
    return children


def read_env(path):
    """Read EnvironmentFile literals; never evaluate shell code or expand variables."""
    result = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if sep and key.strip() in ('ROS_SETUP', 'UNITREE_ROS_SETUP', 'CYCLONEDDS_URI'):
                parts = shlex.split(value, comments=False)
                if len(parts) != 1:
                    raise ValueError('Quote paths containing spaces in '+str(path))
                result[key.strip()] = parts[0]
    return result


def env_value(value):
    if any(ord(c) < 32 for c in str(value)):
        raise ValueError('Control characters in configuration path')
    return '"'+str(value).replace('\\', '\\\\').replace('"', '\\"')+'"'


def find_ros(config, ros=None, workspace=None, home=None):
    home = home or Path.home()
    existing = read_env(config/'bridge.env')
    ros = ros or os.environ.get('ROS_SETUP') or existing.get('ROS_SETUP') or '/opt/ros/foxy/setup.bash'
    explicit = workspace or os.environ.get('UNITREE_ROS_SETUP') or existing.get('UNITREE_ROS_SETUP')
    if explicit and not str(explicit).startswith('/path/to/'):
        candidates = [Path(explicit).expanduser()]
    else:
        candidates = [home/'.local/share/onboardmimic/ros2_ws/install/setup.bash',
                      home/'cyclonedds_ws/install/setup.bash',
                      home/'unitree_ros2/cyclonedds_ws/install/setup.bash']
        candidates += sorted(home.glob('*/install/setup.bash'))
        candidates += sorted(home.glob('*/*/install/setup.bash'))
    valid = []
    for candidate in dict.fromkeys(candidates):
        if candidate.is_file() and probe_ros(Path(ros).expanduser(), candidate)[0]:
            valid.append(candidate)
    if len(valid) == 1:
        return Path(ros).expanduser(), valid[0]
    if len(valid) > 1:
        raise RuntimeError('Multiple ROS workspaces; select --unitree-ros-setup / 检测到多个工作空间，请指定: '+', '.join(map(str, valid)))
    detail = probe_ros(Path(ros).expanduser(), candidates[0])[1]
    raise RuntimeError('ROS dependencies unavailable / ROS 依赖不完整: '+detail+
                       '\nRun / 执行: bash install_scripts/install_ros_dependencies.sh\n'
                       'Then rerun setup / 再运行向导；已有工作空间可用 --ros-setup 和 --unitree-ros-setup 指定。')


def probe_ros(ros, workspace):
    for path in (ros, workspace):
        if not path.is_file():
            return False, 'Missing / 缺少 '+str(path)
    code = '''import rclpy
from unitree_hg.msg import LowCmd, LowState
from unitree_api.msg import Request, Response
from rosidl_generator_py import import_type_support
from ament_index_python.packages import get_package_prefix
for package in ('unitree_hg', 'unitree_api'):
    import_type_support(package)
get_package_prefix('rmw_cyclonedds_cpp')
print('ROS imports OK (no node created)')
'''
    result = subprocess.run(['bash', '--noprofile', '--norc', '-c',
                             'source "$1" >/dev/null && source "$2" >/dev/null && exec /usr/bin/python3 -c "$3"',
                             'ros-check', str(ros), str(workspace), code], capture_output=True, text=True, timeout=30)
    return result.returncode == 0, (result.stderr or result.stdout).strip()[-2000:]


def lan_addresses():
    devices = json.loads(subprocess.check_output(['ip', '-j', 'address', 'show'], text=True, timeout=5))
    return [(d['ifname'], a['local']) for d in devices if 'UP' in d.get('flags', [])
            and d['ifname'] != 'lo' and not d['ifname'].startswith(('docker', 'veth', 'br-'))
            for a in d.get('addr_info', []) if a.get('family') == 'inet'
            and not a['local'].startswith(('127.', '169.254.', '192.168.123.'))]
