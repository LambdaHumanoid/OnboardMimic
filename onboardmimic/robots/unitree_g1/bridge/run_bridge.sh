#!/usr/bin/env bash
# Explicit start only. Never release vendor mode or start a policy.
set -eo pipefail
umask 077
robot="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root="$(cd "$robot/../../.." && pwd)"
cd "$root"
mkdir -p "$root/var"
source "$robot/bridge/environment.sh"
python="$root/.venv/bin/python"
"$python" -m onboardmimic.robots.unitree_g1.bridge.check_conflicts
"$python" -m onboardmimic.robots.unitree_g1.bridge.network --template "$CYCLONEDDS_URI" --output "$root/var/command-dds.xml"
export CYCLONEDDS_URI="file://$root/var/command-dds.xml"
"$python" -m onboardmimic.robots.unitree_g1.continuity.check_feedback
exec "$robot/native_bridge/build/sonic_command_bridge" --physical --udp-port 16663 --cmd-port 16661 --primary-port 16665 --standby-port 16666 --logs "$root/var/logs/native-bridge"
