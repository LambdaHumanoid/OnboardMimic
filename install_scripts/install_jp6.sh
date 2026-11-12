#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# JetPack bindings belong to the system Python, not a user's Conda environment.
exec /usr/bin/python3 "$root/scripts/install_jp6.py" "$@"
