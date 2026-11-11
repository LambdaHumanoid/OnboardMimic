#!/bin/bash
set -euo pipefail
# No Unitree SDK, SONIC token, device mount or movement publisher exists here.
mkdir -p /capture/service
cp -a --no-preserve=ownership /opt/xrt/opt/apps/roboticsservice/. /capture/service/
cd /capture/service
./RoboticsServiceProcess > /capture/xrt-service.log 2>&1 &
service_pid=$!
cleanup() {
 kill "$service_pid" 2>/dev/null || true
 wait "$service_pid" 2>/dev/null || true
 if [[ -d /root/.local/share/PICOBusinessSuitData/log ]]; then
  cp -r /root/.local/share/PICOBusinessSuitData/log /capture/service-internal-log || true
 fi
}
trap cleanup EXIT
record_args=()
if [[ "${PICO_RECORD:-0}" == 1 ]]; then record_args=(--record "/capture/frames-$(date +%s).jsonl"); fi
python3 /app/receiver.py --source sdk --config /config/devices.json --status /capture/status.json "${record_args[@]}" --duration "${PICO_CAPTURE_SECONDS:-60}"
