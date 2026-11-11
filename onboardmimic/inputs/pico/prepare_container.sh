#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
python3 fetch_dependencies.py
if [[ "$(uname -m)" != aarch64 ]]; then
 echo 'Dependencies verified. ARM64 container build must run on the robot; nothing started.'
 exit 0
fi
docker build -t onboardmimic-pico-receiver:20261020 .
echo 'Image built only. No container or robot service started.'
