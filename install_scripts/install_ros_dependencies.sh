#!/usr/bin/env bash
# Optional missing-dependency repair. No robot programs or services are started.
set -euo pipefail
exec /usr/bin/python3 "$(dirname "$0")/../scripts/install_ros_dependencies.py" "$@"
