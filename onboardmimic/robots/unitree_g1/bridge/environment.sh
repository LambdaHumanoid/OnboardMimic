#!/usr/bin/env bash
# Sourced by manually started bridge/readers, never by the installer.
: "${ROS_SETUP:?Set ROS_SETUP in bridge.env}"
: "${UNITREE_ROS_SETUP:?Set UNITREE_ROS_SETUP in bridge.env}"
: "${CYCLONEDDS_URI:?Set CYCLONEDDS_URI to a robot-network XML file}"
source "$ROS_SETUP"
source "$UNITREE_ROS_SETUP"
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI
