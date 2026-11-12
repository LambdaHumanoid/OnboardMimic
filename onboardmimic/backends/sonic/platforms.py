"""Source-build environment selection; no robot communication or service changes."""
from pathlib import Path
import platform
import re


from onboardmimic.core.host_platform import os_release


def source_environment_errors(facts):
    errors = []
    if facts.get('arch') != 'aarch64':
        errors.append('Target source installation requires aarch64 Jetson')
    # Carrier boards may report a vendor name instead of the compute module.
    orin = 'Orin NX' in facts.get('board', '') or 'orin' in facts.get('gpu', '').lower()
    if not orin or facts.get('memory_kib', 0) < 12*1024*1024:
        errors.append('Target is Orin NX 16 GB')
    ubuntu, l4t = facts.get('ubuntu', ''), facts.get('l4t', '')
    trt, cuda = facts.get('tensorrt', ''), facts.get('cuda_runtime', 0)
    jp5 = ubuntu == '20.04' and l4t.startswith('35.') and trt.startswith('8.5.') and 11000 <= cuda < 12000
    jp6 = ubuntu == '22.04' and l4t.startswith('36.') and trt.startswith('10.') and 12000 <= cuda < 13000
    if not (jp5 or jp6):
        errors.append('Require JP5 / TensorRT 8.5 / CUDA 11 or JP6 / TensorRT 10 / CUDA 12')
    return errors


def basic_environment():
    release = os_release()
    l4t = Path('/etc/nv_tegra_release')
    match = re.search(r'# R(\d+).*?REVISION:\s*([\d.]+)', l4t.read_text()) if l4t.exists() else None
    board = Path('/proc/device-tree/model')
    memory = re.search(r'^MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)
    return {'arch': platform.machine(), 'ubuntu': release.get('VERSION_ID', '') if release.get('ID') == 'ubuntu' else '',
            'l4t': '.'.join(match.groups()) if match else '',
            'board': board.read_text().strip('\0\n') if board.exists() else '',
            'memory_kib': int(memory[1]) if memory else 0, 'python': platform.python_version()}
