#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python="${PYTHON:-python3}"
mode="${1:---all}"
case "$mode" in --all|--core|--controller) ;; *) echo 'Usage: scripts/test.sh [--all|--core|--controller]' >&2; exit 2 ;; esac
python="$("$python" -c 'import os, sys; print(os.path.abspath(sys.executable))')"
"$python" -m unittest discover -s tests/runtime -t . -p 'test_*.py' -q
(cd onboardmimic/backends/sonic/deploy/onboard && "$python" validate_safety_source.py)
mapfile -t base_tests < <("$python" -c 'from pathlib import Path; print("\n".join("tests."+p.stem for p in sorted(Path("tests").glob("test_*.py"))))')
"$python" -m unittest "${base_tests[@]}" -q
modules=(console protection deployment)
if [[ "$mode" != --core ]]; then modules+=(pico sonic); fi
for module in "${modules[@]}"; do
    "$python" -m unittest discover -s "tests/$module" -t . -p 'test_*.py' -q
done
if [[ "$mode" == --all ]]; then
    PYTHONPATH="$PWD/lab/src${PYTHONPATH:+:$PYTHONPATH}" "$python" -m unittest discover -s lab/tests -p 'test_*.py' -q
    "$python" scripts/check_oml_execution.py
fi
