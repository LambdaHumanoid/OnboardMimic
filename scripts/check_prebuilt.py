"""Compatibility entrypoint for the SONIC release profile."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from onboardmimic.backends.sonic.check_prebuilt import main

if __name__ == "__main__":
    main()
