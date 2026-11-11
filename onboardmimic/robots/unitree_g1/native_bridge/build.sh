#!/usr/bin/env bash
# Compile only. Never start readers, command bridge or controller.
set -eo pipefail
: "${ROS_SETUP:?Set ROS_SETUP to ROS2 setup.bash}"
: "${UNITREE_ROS_SETUP:?Set UNITREE_ROS_SETUP to the unitree_hg workspace setup.bash}"
source "$ROS_SETUP"
source "$UNITREE_ROS_SETUP"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmake -S "$root" -B "$root/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$root/build" --parallel 2
"$root/build/core_test"
"$root/build/authority_test"
