#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="${MISSION_CONTROL_WORKSPACE:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
MAP_NAME="${1:-${MISSION_CONTROL_DEFAULT_MAP:-atrium_navigation}}"
MAP_NAME="${MAP_NAME%.yaml}"
MAP_DIRECTORY="${MISSION_CONTROL_ROS2_MAP_DIRECTORY:-${WORKSPACE_ROOT}/src/my_bot/maps}"
ROBOT_ID="${MISSION_CONTROL_ROBOT_ID:-robot-1}"
PORT="${PORT:-8000}"
BASE_URL="http://127.0.0.1:${PORT}"
SERVER_START_TIMEOUT_S="${SERVER_START_TIMEOUT_S:-30}"
NAV_READY_TIMEOUT_S="${NAV_READY_TIMEOUT_S:-60}"
LOCK_FILE="${MISSION_CONTROL_LOCK_FILE:-/tmp/mission-control-navigation-ui-${UID}-${PORT}.lock}"
SERVER_PID=""
UI_ONLY="${MISSION_CONTROL_UI_ONLY:-false}"

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
  echo "Navigation UI launch failed: $*" >&2
  exit 1
}

command -v curl >/dev/null 2>&1 || fail "curl is required."
command -v flock >/dev/null 2>&1 || fail "flock is required (install util-linux)."
command -v setsid >/dev/null 2>&1 || fail "setsid is required (install util-linux)."
command -v timeout >/dev/null 2>&1 || fail "timeout is required (install coreutils)."

if [[ ! "${MAP_NAME}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  fail "invalid map name '${MAP_NAME}'. Pass a saved map name, not a path."
fi
if [[ ! -f "${MAP_DIRECTORY}/${MAP_NAME}.yaml" ]]; then
  fail "map not found: ${MAP_DIRECTORY}/${MAP_NAME}.yaml"
fi
LAYERED_MAP=false
if [[ "${MAP_NAME}" == *_navigation ]]; then
  MAP_PROFILE="${MAP_NAME%_navigation}"
  [[ -f "${MAP_DIRECTORY}/${MAP_PROFILE}_keepout.yaml" ]] \
    || fail "keepout map not found: ${MAP_DIRECTORY}/${MAP_PROFILE}_keepout.yaml"
  [[ -f "${MAP_DIRECTORY}/${MAP_PROFILE}_display.yaml" ]] \
    || fail "display map not found: ${MAP_DIRECTORY}/${MAP_PROFILE}_display.yaml"
  LAYERED_MAP=true
fi
if [[ ! -x "${SCRIPT_DIR}/.venv-local/bin/python3" ]]; then
  fail "local Python environment is missing. Run ${SCRIPT_DIR}/setup_env_linux.sh once."
fi
if [[ ! "${UI_ONLY}" =~ ^(1|true|yes|on)$ ]]; then
  if [[ ! -f "${WORKSPACE_ROOT}/install/setup.bash" ]]; then
    fail "workspace is not built: ${WORKSPACE_ROOT}/install/setup.bash is missing."
  fi
  if ! ROS_SETUP_FILE="$(find_ros_setup)"; then
    fail "no ROS 2 setup file found. Set ROS_SETUP_FILE or ROS_DISTRO."
  fi
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  fail "another navigation UI launcher is already running on port ${PORT}."
fi

if curl -fsS "${BASE_URL}/health" >/dev/null 2>&1; then
  fail "a Mission Control server is already running at ${BASE_URL}. Stop it first."
fi

export MISSION_CONTROL_ROBOT_ID="${ROBOT_ID}"
if [[ "${UI_ONLY}" =~ ^(1|true|yes|on)$ ]]; then
  export MISSION_CONTROL_ROBOT_BACKEND=sim
  export MISSION_CONTROL_UI_MAP="${MAP_DIRECTORY}/${MAP_NAME}.yaml"
else
  export ROS_SETUP_FILE
  export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
  export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
  export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
  export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://${WORKSPACE_ROOT}/install/my_bot/share/my_bot/config/cyclonedds.xml}"
  export MISSION_CONTROL_ROBOT_BACKEND=ros2
  export MISSION_CONTROL_ROS2_LAUNCHER_MODE=local
  export MISSION_CONTROL_ROS2_CENTRAL_WORKSPACE="${WORKSPACE_ROOT}"
  export MISSION_CONTROL_ROS2_MAPPING_WORKSPACE="${WORKSPACE_ROOT}"
  export MISSION_CONTROL_ROS2_NAV_WORKSPACE="${WORKSPACE_ROOT}"
  export MISSION_CONTROL_ROS2_MAP_DIRECTORY="${MAP_DIRECTORY}"
  export MISSION_CONTROL_ROS2_LAUNCH_RVIZ="${MISSION_CONTROL_ROS2_LAUNCH_RVIZ:-false}"

  source_relaxed "${ROS_SETUP_FILE}"
  source_relaxed "${WORKSPACE_ROOT}/install/setup.bash"
fi

trap shutdown_server EXIT INT TERM HUP

cd "${SCRIPT_DIR}"
echo "Starting Mission Control at ${BASE_URL}..."
setsid ./run_server.sh 9>&- &
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

if [[ ! "${UI_ONLY}" =~ ^(1|true|yes|on)$ ]]; then
  payload=$(printf \
    '{"command":"launch_nav","map_name":"%s","command_source":{"type":"system","id":"launch-navigation-ui"}}' \
    "${MAP_NAME}")
  response=$(curl -sS -w $'\n%{http_code}' \
    -X POST "${BASE_URL}/robots/${ROBOT_ID}/system-command" \
    -H 'Content-Type: application/json' \
    --data "${payload}")
  http_status="${response##*$'\n'}"
  response_body="${response%$'\n'*}"

  if [[ ! "${http_status}" =~ ^2[0-9][0-9]$ ]]; then
    fail "map/Nav2 request returned HTTP ${http_status}: ${response_body}"
  fi
fi

echo
echo "UI is ready with map '${MAP_NAME}'."
if [[ "${LAYERED_MAP}" == true ]]; then
  echo "Navigation: ${MAP_PROFILE}_navigation | Keepout: ${MAP_PROFILE}_keepout | UI: ${MAP_PROFILE}_display"
fi
echo "UI: ${BASE_URL}/ui"
if [[ "${UI_ONLY}" =~ ^(1|true|yes|on)$ ]]; then
  echo "UI-only mode: the display map is loaded directly; ROS, Nav2, AMCL, and TF are not started."
  echo "Press Ctrl+C here to stop the UI."
else
  echo "The map remains available when the robot is offline."
  echo "Press Ctrl+C here to stop the UI and its Nav2 process."
fi

if [[ "${OPEN_UI_BROWSER:-true}" =~ ^(1|true|yes|on)$ ]] \
    && command -v xdg-open >/dev/null 2>&1 \
    && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  xdg-open "${BASE_URL}/ui" 9>&- >/dev/null 2>&1 &
fi

if [[ "${UI_ONLY}" =~ ^(1|true|yes|on)$ ]]; then
  wait "${SERVER_PID}"
  server_status=$?
  SERVER_PID=""
  trap - EXIT INT TERM HUP
  exit "${server_status}"
fi

echo "Waiting for Nav2 and AMCL in the background startup flow..."
deadline=$((SECONDS + NAV_READY_TIMEOUT_S))
while true; do
  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    wait "${SERVER_PID}" || true
    fail "Mission Control exited while Nav2 was starting."
  fi

  services="$(timeout 5 ros2 service list 2>/dev/null || true)"
  actions="$(timeout 5 ros2 action list 2>/dev/null || true)"
  topics="$(timeout 5 ros2 topic list 2>/dev/null || true)"
  amcl_state="$(timeout 5 ros2 lifecycle get /amcl 2>/dev/null || true)"
  navigator_state="$(timeout 5 ros2 lifecycle get /bt_navigator 2>/dev/null || true)"
  layer_topics_ready=true
  if [[ "${LAYERED_MAP}" == true ]] \
      && { ! grep -Fxq '/keepout_filter_mask' <<<"${topics}" \
        || ! grep -Fxq '/costmap_filter_info' <<<"${topics}" \
        || ! grep -Fxq '/display_map' <<<"${topics}"; }; then
    layer_topics_ready=false
  fi
  if grep -Fxq '/reinitialize_global_localization' <<<"${services}" \
      && grep -Fxq '/set_initial_pose' <<<"${services}" \
      && grep -Fxq '/request_nomotion_update' <<<"${services}" \
      && grep -Fxq '/navigate_to_pose' <<<"${actions}" \
      && grep -Fxq '/map' <<<"${topics}" \
      && [[ "${layer_topics_ready}" == true ]] \
      && grep -Fq 'active [3]' <<<"${amcl_state}" \
      && grep -Fq 'active [3]' <<<"${navigator_state}"; then
    break
  fi

  if (( SECONDS >= deadline )); then
    echo "Warning: Nav2/AMCL is not ready after ${NAV_READY_TIMEOUT_S}s; the map UI will remain available." >&2
    break
  fi
  sleep 1
done

if (( SECONDS < deadline )); then
  echo "Navigation is ready with map '${MAP_NAME}'. Localization will match stationary lidar scans."
fi

wait "${SERVER_PID}"
server_status=$?
SERVER_PID=""
trap - EXIT INT TERM HUP
exit "${server_status}"
