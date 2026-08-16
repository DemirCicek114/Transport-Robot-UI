#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="${MISSION_CONTROL_WORKSPACE:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
MAP_NAME="${1:-${MISSION_CONTROL_DEFAULT_MAP:-atrium_navigation}}"
MAP_NAME="${MAP_NAME%.yaml}"
MAP_DIRECTORY="${MISSION_CONTROL_ROS2_MAP_DIRECTORY:-${WORKSPACE_ROOT}/src/my_bot/maps}"
PORT="${PORT:-8000}"
BASE_URL="http://127.0.0.1:${PORT}"
SERVER_START_TIMEOUT_S="${SERVER_START_TIMEOUT_S:-30}"
SERVER_PID=""

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

shutdown_server() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill -INT "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}

fail() {
  echo "Remote autonomy UI launch failed: $*" >&2
  exit 1
}

command -v curl >/dev/null 2>&1 || fail "curl is required."
command -v setsid >/dev/null 2>&1 || fail "setsid is required."

[[ -f "${WORKSPACE_ROOT}/install/setup.bash" ]] \
  || fail "workspace is not built: ${WORKSPACE_ROOT}/install/setup.bash is missing."
[[ -f "${MAP_DIRECTORY}/${MAP_NAME}.yaml" ]] \
  || fail "map not found: ${MAP_DIRECTORY}/${MAP_NAME}.yaml"
[[ -x "${SCRIPT_DIR}/.venv-local/bin/python3" ]] \
  || fail "run ${SCRIPT_DIR}/setup_env_linux.sh first."
if ! ROS_SETUP_FILE="$(find_ros_setup)"; then
  fail "no ROS 2 setup file found."
fi
if curl -fsS "${BASE_URL}/health" >/dev/null 2>&1; then
  fail "Mission Control is already running at ${BASE_URL}."
fi

export ROS_SETUP_FILE
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://${WORKSPACE_ROOT}/install/my_bot/share/my_bot/config/cyclonedds.xml}"
export MISSION_CONTROL_ROBOT_BACKEND=ros2
export MISSION_CONTROL_ROBOT_ID="${MISSION_CONTROL_ROBOT_ID:-robot-1}"
export MISSION_CONTROL_ROS2_LAUNCHER_MODE=external
export MISSION_CONTROL_ROS2_EXTERNAL_MAP_NAME="${MAP_NAME}"
export MISSION_CONTROL_ROS2_MAP_DIRECTORY="${MAP_DIRECTORY}"
export HOST=127.0.0.1
export PORT

source_relaxed "${ROS_SETUP_FILE}"
source_relaxed "${WORKSPACE_ROOT}/install/setup.bash"

trap shutdown_server EXIT INT TERM HUP
cd "${SCRIPT_DIR}"
setsid ./run_server.sh &
SERVER_PID=$!

deadline=$((SECONDS + SERVER_START_TIMEOUT_S))
until curl -fsS "${BASE_URL}/health" >/dev/null 2>&1; do
  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    wait "${SERVER_PID}" || true
    fail "Mission Control exited before becoming healthy."
  fi
  if (( SECONDS >= deadline )); then
    fail "Mission Control did not become healthy within ${SERVER_START_TIMEOUT_S}s."
  fi
  sleep 0.5
done

echo
echo "Laptop UI is attached to Pi autonomy."
echo "UI: ${BASE_URL}/ui"
echo "This launcher does not start or stop Nav2."
echo "Press Ctrl+C here to stop only the laptop UI backend."

if [[ "${OPEN_UI_BROWSER:-true}" =~ ^(1|true|yes|on)$ ]] \
    && command -v xdg-open >/dev/null 2>&1 \
    && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  xdg-open "${BASE_URL}/ui" >/dev/null 2>&1 &
fi

wait "${SERVER_PID}"
server_status=$?
SERVER_PID=""
trap - EXIT INT TERM HUP
exit "${server_status}"
