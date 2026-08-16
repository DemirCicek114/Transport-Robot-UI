#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOT_WORKSPACE="${ROBOT_WORKSPACE:-/home/$(id -un)/robot_ws}"
MAP_NAME="${MISSION_CONTROL_DEFAULT_MAP:-atrium_navigation}"
MAP_NAME="${MAP_NAME%.yaml}"
MAP_DIRECTORY="${MISSION_CONTROL_ROS2_MAP_DIRECTORY:-${ROBOT_WORKSPACE}/src/catering_bot/maps}"

source_relaxed() {
  set +u
  # shellcheck disable=SC1090
  source "$1"
  set -u
}

find_ros_setup() {
  if [[ -n "${ROS_SETUP_FILE:-}" && -f "${ROS_SETUP_FILE}" ]]; then
    printf '%s\n' "${ROS_SETUP_FILE}"
    return
  fi
  if [[ -n "${ROS_DISTRO:-}" && -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]]; then
    printf '%s\n' "/opt/ros/${ROS_DISTRO}/setup.bash"
    return
  fi
  if [[ -f /opt/ros/humble/setup.bash ]]; then
    printf '%s\n' /opt/ros/humble/setup.bash
    return
  fi
  if [[ -f /opt/ros/jazzy/setup.bash ]]; then
    printf '%s\n' /opt/ros/jazzy/setup.bash
    return
  fi
  return 1
}

if ! ROS_SETUP_FILE="$(find_ros_setup)"; then
  echo "No ROS 2 setup file was found. Set ROS_SETUP_FILE or ROS_DISTRO." >&2
  exit 1
fi
if [[ ! -f "${ROBOT_WORKSPACE}/install/setup.bash" ]]; then
  echo "Robot workspace is not built: ${ROBOT_WORKSPACE}/install/setup.bash is missing." >&2
  exit 1
fi
if [[ ! -f "${MAP_DIRECTORY}/${MAP_NAME}.yaml" ]]; then
  echo "Navigation map not found: ${MAP_DIRECTORY}/${MAP_NAME}.yaml" >&2
  exit 1
fi

export ROS_SETUP_FILE
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://${ROBOT_WORKSPACE}/install/my_bot/share/my_bot/config/cyclonedds.xml}"
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8000}"
export MISSION_CONTROL_ROBOT_BACKEND=ros2
export MISSION_CONTROL_ROBOT_ID="${MISSION_CONTROL_ROBOT_ID:-robot-1}"
export MISSION_CONTROL_ROS2_LAUNCHER_MODE=external
export MISSION_CONTROL_ROS2_EXTERNAL_MAP_NAME="${MAP_NAME}"
export MISSION_CONTROL_ROS2_MAP_DIRECTORY="${MAP_DIRECTORY}"

source_relaxed "${ROS_SETUP_FILE}"
source_relaxed "${ROBOT_WORKSPACE}/install/setup.bash"

cd "${SCRIPT_DIR}"
exec ./run_server.sh
