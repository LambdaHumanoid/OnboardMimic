"""Compatibility entrypoint; implementation lives in its adapter module."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from onboardmimic.robots.unitree_g1.install_dependencies import main

if __name__ == "__main__":
    main()
