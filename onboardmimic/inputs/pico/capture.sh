#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
config="${1:?Usage: bash capture.sh /absolute/path/devices.json}"
config=$(realpath "$config")
run_dir="${PICO_RUN_DIR:-$PWD/runs/live}"
run_dir=$(realpath -m "$run_dir")
mkdir -p "$run_dir"
# Only tracking network and capture files; no --privileged, /dev, Docker socket,
# NVIDIA runtime, robot credentials or robot source directories are mounted.
docker run --runtime runc --rm --init --name onboardmimic-pico-receive-only --network host --cap-drop ALL --security-opt no-new-privileges \
 --env "PICO_CAPTURE_SECONDS=${PICO_CAPTURE_SECONDS:-60}" \
 --env "PICO_RECORD=${PICO_RECORD:-0}" \
 --mount "type=bind,src=$config,dst=/config/devices.json,readonly" \
 --mount "type=bind,src=$run_dir,dst=/capture" \
 onboardmimic-pico-receiver:20261020 bash /app/container_capture.sh
