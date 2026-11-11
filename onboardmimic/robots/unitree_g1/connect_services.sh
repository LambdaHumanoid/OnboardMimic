#!/usr/bin/env bash
# Explicit operator request only. No preparation or policy start.
set -eo pipefail
robot="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$robot/../../.." && pwd)"
cd "$root"
source "$robot/bridge/environment.sh"
exec "$root/.venv/bin/python" -m onboardmimic.robots.unitree_g1.connect_services --release-vendor-mode
