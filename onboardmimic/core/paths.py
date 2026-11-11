"""Installation-relative paths; no robot addresses or controller imports."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'var'


def state_dir():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    return STATE
