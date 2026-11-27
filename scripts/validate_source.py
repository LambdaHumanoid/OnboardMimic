#!/usr/bin/env python3
"""Inspect source-build software, optionally run inference; never drive the robot."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from onboardmimic.backends.sonic.source_validation import main

if __name__ == '__main__':
    raise SystemExit(main())
