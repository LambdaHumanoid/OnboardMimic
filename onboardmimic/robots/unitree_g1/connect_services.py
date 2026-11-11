"""Explicit operator connection. Never prepares posture or starts the policy."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

from onboardmimic.core.paths import STATE as ROOT
from onboardmimic.robots.unitree_g1.bridge.check_conflicts import check as check_conflicts
from onboardmimic.robots.unitree_g1.bridge.motion_mode import MotionMode, release_mode
from onboardmimic.robots.unitree_g1.bridge.network import configure, detect
from onboardmimic.robots.unitree_g1.continuity.check_feedback import check as check_feedback
from .guard import physical_guard
from onboardmimic.robots.unitree_g1.preflight import service

BRIDGE = 'onboardmimic-bridge.service'
READERS = ['onboardmimic-state-'+role+'.service' for role in ('primary','standby','relay')]


@contextmanager
def owner_lock(path='/tmp/sonic-motor-control.lock'):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError('Invalid motor owner lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another bridge owns motor control; incumbent unchanged')
        yield
    finally:
        os.close(fd)


def connect(ops):
    # Reuse a healthy idle bridge; never restart it or release mode underneath it.
    if ops.bridge_state() == 'active':
        ops.ready()
        ops.report('ready', '控制服务已连接')
        return
    with ops.lock():
        ops.conflicts()
        ops.configure()
        ops.report('feedback', '正在检查机器人反馈')
        ops.feedback()
        ops.report('release', '正在释放宇树运动模式')
        ops.release()
    # Native bridge acquires the same owner lock itself. A racing owner is refused,
    # never stopped. Keep the incumbent's runtime observer advisory-only.
    ops.conflicts()
    ops.report('bridge', '正在等待桥接就绪')
    ops.start_bridge()
    ops.wait_ready()
    ops.report('ready', '控制服务已连接')


class Operations:
    def __init__(self):
        ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path = ROOT/'connect-status.json'

    def report(self, phase, detail):
        data = dict(phase=phase, detail=detail, updated_at=time.time())
        if phase == 'ready':
            bridge = service(BRIDGE, user=True)
            if bridge.get('ActiveState') != 'active' or int(bridge.get('MainPID', '0')) <= 0:
                raise RuntimeError('Bridge exited before connection completed')
            data['bridge_pid'] = int(bridge['MainPID'])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False)+'\n')
        temp.replace(self.path)
        print(json.dumps(data, ensure_ascii=False), flush=True)

    def systemctl(self, *args):
        subprocess.run(['systemctl','--user',*args], check=True, timeout=15)

    def bridge_state(self):
        result = service(BRIDGE, user=True)
        if result.get('ActiveState') not in ('active','inactive','failed'):
            raise RuntimeError('Bridge state unknown or changing; wait before connecting')
        return result['ActiveState']

    lock = staticmethod(owner_lock)
    conflicts = staticmethod(check_conflicts)

    def configure(self):
        xml = ROOT/'command-dds.xml'
        configure(os.environ['CYCLONEDDS_URI'], xml, detect())
        os.environ['CYCLONEDDS_URI'] = xml.resolve().as_uri()

    def feedback(self):
        self.systemctl('start', *READERS)
        check_feedback()

    def release(self):
        client = MotionMode()
        try:
            print(json.dumps(release_mode(client)), flush=True)
        finally:
            client.close()

    def start_bridge(self):
        self.systemctl('start', BRIDGE)
        self.systemctl('start', 'onboardmimic.service')

    def ready(self):
        physical_guard()
        check_feedback(timeout=2.)

    def wait_ready(self):
        deadline, error = time.monotonic()+15., 'Bridge not ready'
        while time.monotonic() < deadline:
            if self.bridge_state() == 'failed':
                raise RuntimeError('Bridge failed; inspect onboardmimic-bridge journal')
            try:
                self.ready()
                return
            except RuntimeError as exc:
                error = str(exc)
                time.sleep(.1)
        raise RuntimeError('Bridge readiness timed out: '+error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-vendor-mode', action='store_true')
    parser.add_argument('--check-only', action='store_true', help='Only query Unitree mode; no release or service start')
    args = parser.parse_args()
    if args.check_only:
        ops = Operations()
        ops.configure()
        client = MotionMode()
        try:
            print(json.dumps(dict(read_only=True, mode=client.check_mode())))
        finally:
            client.close()
        return
    if not args.release_vendor_mode:
        parser.error('Explicit --release-vendor-mode is required')
    ops = Operations()
    try:
        connect(ops)
    except (Exception, SystemExit) as exc:
        ops.report('error', str(exc))
        raise


if __name__ == '__main__':
    main()
