"""Remove only an abandoned protection socket while holding both writer locks."""
import errno
import fcntl
import os
from pathlib import Path
import socket
import stat

LOCKS = ('/tmp/onboardmimic-motor-control.lock', '/tmp/sonic-motor-control.lock')


def cleanup(path, locks=LOCKS):
    path = Path(path)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise RuntimeError('Protection socket directory must be private and owned')
    fds = []
    try:
        for name in locks:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            fds.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError('Invalid motor ownership lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try: before = path.lstat()
        except FileNotFoundError: return False
        if not stat.S_ISSOCK(before.st_mode) or before.st_uid != os.getuid():
            raise RuntimeError('Refusing to replace a non-socket or foreign endpoint')
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as probe:
            probe.settimeout(.2)
            try: probe.connect(str(path))
            except OSError as exc:
                if exc.errno != errno.ECONNREFUSED: raise
            else: raise RuntimeError('Protection endpoint has a live listener')
        after = path.lstat()
        if (before.st_dev,before.st_ino) != (after.st_dev,after.st_ino):
            raise RuntimeError('Protection endpoint changed during verification')
        path.unlink()
        return True
    finally:
        for fd in reversed(fds): os.close(fd)


if __name__ == '__main__':
    import sys
    if cleanup(sys.argv[1]): print('Removed abandoned protection socket under both writer locks', flush=True)
