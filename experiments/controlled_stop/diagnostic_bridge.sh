#!/usr/bin/env bash
set -eo pipefail
experiment="$(cd "$(dirname "$0")" && pwd)"
source /opt/ros/foxy/setup.bash
source /home/unitree/cyclonedds_ws/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=94
export CYCLONEDDS_URI='<CycloneDDS><Domain><General><NetworkInterfaceAddress>lo</NetworkInterfaceAddress><AllowMulticast>false</AllowMulticast></General></Domain></CycloneDDS>'
export ONBOARDMIMIC_EXPERIMENT_SOCKET="$experiment/diagnostic.sock"
exec "$experiment/bridge_build/sonic_command_bridge" --udp-port 19663 --cmd-port 19661 --primary-port 19665 --standby-port 19666 --logs "$experiment/logs/diagnostic"
