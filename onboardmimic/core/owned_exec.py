"""Linux owned-child launcher; dies if the gateway disappears, including SIGKILL."""
import ctypes
import os
import signal
import sys


def main():
    expected_parent = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), 'prctl(PR_SET_PDEATHSIG)')
    if os.getppid() != expected_parent:
        raise SystemExit('Gateway parent exited before child setup')
    os.execv(sys.argv[2], sys.argv[2:])


if __name__ == '__main__':
    main()
