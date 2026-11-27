#!/usr/bin/env bash
# Normal SONIC transport, plus manual handoff IPC in the experimental binary.
set -eo pipefail
umask 077
experiment="$(cd "$(dirname "$0")" && pwd)"
root="$1"
cd "$root"
source "$HOME/.config/onboardmimic/bridge.env"
source "$root/onboardmimic/robots/unitree_g1/bridge/environment.sh"
"$root/.venv/bin/python" -m onboardmimic.robots.unitree_g1.bridge.check_conflicts
"$root/.venv/bin/python" -m onboardmimic.robots.unitree_g1.bridge.network --template "$CYCLONEDDS_URI" --output "$experiment/command-dds.xml"
export CYCLONEDDS_URI="file://$experiment/command-dds.xml"
export ONBOARDMIMIC_EXPERIMENT_SOCKET="$experiment/handoff.sock"
# After conflict inspection only. A live bridge prevents reaching this line.
rm -f "$ONBOARDMIMIC_EXPERIMENT_SOCKET"
"$root/.venv/bin/python" -m onboardmimic.robots.unitree_g1.continuity.check_feedback
exec "$experiment/bridge_build/sonic_command_bridge" --physical --udp-port 16663 --cmd-port 16661 --primary-port 16665 --standby-port 16666 --logs "$experiment/logs/bridge"
