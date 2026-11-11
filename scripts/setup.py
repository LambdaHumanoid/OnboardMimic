#!/usr/bin/env python3
"""Select a supported robot/S0 installation recipe before making changes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import sys
from onboardmimic.core.registry import select, DEFAULT_ROBOT, DEFAULT_BACKEND


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--robot', default=DEFAULT_ROBOT)
    parser.add_argument('--backend', default=DEFAULT_BACKEND)
    args, rest = parser.parse_known_args(argv)
    robot, backend = select(args.robot, args.backend)
    return backend.installer(robot)(rest)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError) as exc:
        raise SystemExit('Setup incomplete / 安装未完成: '+str(exc))
