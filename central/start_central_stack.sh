#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_WORKSPACE="$(cd "${REPOSITORY_ROOT}/.." && pwd)"
MODE="${1:-navigation}"
CONFIG_FILE="${INTELLITROLLEY_CONFIG_FILE:-${HOME}/.config/intellitrolley/central.env}"

if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

WORKSPACE="${INTELLITROLLEY_PACKAGED_WORKSPACE:-${INTELLITROLLEY_WORKSPACE:-${DEFAULT_WORKSPACE}}}"
if [[ -n "${INTELLITROLLEY_PACKAGED_WORKSPACE:-}" ]]; then
  MISSION_CONTROL_DIR="${WORKSPACE}/robot_server/mission_control_poc"
else
  MISSION_CONTROL_DIR="${INTELLITROLLEY_MISSION_CONTROL_DIR:-${REPOSITORY_ROOT}/mission_control_poc}"
fi
DATA_DIR="${INTELLITROLLEY_DATA_DIR:-${HOME}/.local/share/intellitrolley}"
STATE_DIR="${INTELLITROLLEY_STATE_DIR:-${HOME}/.local/state/intellitrolley}"
LOG_DIR="${INTELLITROLLEY_LOG_DIR:-${DATA_DIR}/logs}"
SOURCE_MAP_DIR="${WORKSPACE}/src/my_bot/maps"
MAP_DIR="${INTELLITROLLEY_MAP_DIR:-${DATA_DIR}/maps}"
MAP_NAME="${INTELLITROLLEY_MAP_NAME:-atrium_navigation}"
MAP_NAME="${MAP_NAME%.yaml}"
HOST="${INTELLITROLLEY_BIND_HOST:-127.0.0.1}"
PORT="${INTELLITROLLEY_PORT:-8000}"
RVIZ="${INTELLITROLLEY_LAUNCH_RVIZ:-false}"
ROS_SETUP_FILE="${ROS_SETUP_FILE:-/opt/ros/humble/setup.bash}"
WORKSPACE_SETUP="${WORKSPACE}/install/setup.bash"
DB_PATH="${MISSION_CONTROL_DB_PATH:-${DATA_DIR}/mission_control.sqlite3}"
DESTINATIONS_PATH="${MISSION_CONTROL_DESTINATIONS_PATH:-${DATA_DIR}/destinations.yaml}"
LOCK_FILE="${STATE_DIR}/central.lock"
PID_FILE="${STATE_DIR}/central.pid"
ROS_PID=""
SERVER_PID=""
ROS_LAUNCHER_PID=""
SERVER_LAUNCHER_PID=""
ROS_CHILD_PID_FILE="${STATE_DIR}/central-ros.pid"
SERVER_CHILD_PID_FILE="${STATE_DIR}/central-server.pid"
SHUTTING_DOWN=false

fail() {
  echo "IntelliTrolley central start failed: $*" >&2
  exit 1
}

source_relaxed() {
  set +u
  # shellcheck disable=SC1090
  source "$1"
  set -u
}

process_group_is_running() {
  local process_group_id="$1"
  [[ -n "${process_group_id}" ]] \
    && kill -0 -- "-${process_group_id}" 2>/dev/null
}

stop_process_group() {
  local process_group_id="$1"
  local signal_name="$2"
  if process_group_is_running "${process_group_id}"; then
    kill "-${signal_name}" -- "-${process_group_id}" 2>/dev/null || true
  fi
}

wait_for_exit() {
  local process_group_id="$1"
  local attempts=50
  while (( attempts > 0 )) && process_group_is_running "${process_group_id}"; do
    sleep 0.1
    attempts=$((attempts - 1))
  done

  if process_group_is_running "${process_group_id}"; then
    stop_process_group "${process_group_id}" TERM
    attempts=50
    while (( attempts > 0 )) && process_group_is_running "${process_group_id}"; do
      sleep 0.1
      attempts=$((attempts - 1))
    done
  fi

  if process_group_is_running "${process_group_id}"; then
    echo "Process group ${process_group_id} ignored INT and TERM; forcing exit." >&2
    stop_process_group "${process_group_id}" KILL
  fi
  wait "${process_group_id}" 2>/dev/null || true
}

start_owned_process() {
  local process_result_name="$1"
  local launcher_result_name="$2"
  local child_pid_file="$3"
  local working_directory="$4"
  local output_file="$5"
  shift 5

  rm -f "${child_pid_file}"
  (
    # The supervisor alone owns the flock. Children must not keep it open
    # after the supervisor exits.
    exec 9>&-
    cd "${working_directory}"
    exec setsid bash -c '
      child_pid_file="$1"
      shift
      printf "%s\n" "$$" > "${child_pid_file}"
      exec "$@"
    ' intellitrolley-child "${child_pid_file}" "$@"
  ) >"${output_file}" 2>&1 &
  local launcher_pid=$!

  local attempts=100
  while (( attempts > 0 )) && [[ ! -s "${child_pid_file}" ]]; do
    sleep 0.05
    attempts=$((attempts - 1))
  done
  [[ -s "${child_pid_file}" ]] \
    || fail "owned process did not report its PID; inspect ${output_file}"

  local child_pid
  child_pid="$(tr -d '[:space:]' < "${child_pid_file}")"
  [[ "${child_pid}" =~ ^[0-9]+$ ]] \
    || fail "owned process reported an invalid PID: ${child_pid}"
  kill -0 "${child_pid}" 2>/dev/null \
    || fail "owned process exited during startup; inspect ${output_file}"

  printf -v "${process_result_name}" '%s' "${child_pid}"
  printf -v "${launcher_result_name}" '%s' "${launcher_pid}"
}

publish_navigation_zeros() {
  if [[ "${MODE}" != "navigation" ]] || ! command -v ros2 >/dev/null 2>&1; then
    return
  fi
  timeout 3s ros2 topic pub \
    --rate 20 \
    --times 8 \
    --wait-matching-subscriptions 0 \
    /cmd_vel_nav_raw \
    geometry_msgs/msg/Twist \
    '{}' >/dev/null 2>&1 || true
}

shutdown_stack() {
  if [[ "${SHUTTING_DOWN}" == true ]]; then
    return
  fi
  SHUTTING_DOWN=true
  trap - EXIT INT TERM HUP

  echo "Stopping IntelliTrolley central stack..."
  publish_navigation_zeros
  if [[ -n "${SERVER_PID}" ]]; then
    stop_process_group "${SERVER_PID}" INT
    wait_for_exit "${SERVER_PID}"
  fi
  if [[ -n "${ROS_PID}" ]]; then
    stop_process_group "${ROS_PID}" INT
    wait_for_exit "${ROS_PID}"
  fi
  if [[ -n "${SERVER_LAUNCHER_PID}" ]]; then
    wait "${SERVER_LAUNCHER_PID}" 2>/dev/null || true
  fi
  if [[ -n "${ROS_LAUNCHER_PID}" ]]; then
    wait "${ROS_LAUNCHER_PID}" 2>/dev/null || true
  fi
  rm -f "${ROS_CHILD_PID_FILE}" "${SERVER_CHILD_PID_FILE}"
  if [[ -f "${PID_FILE}" ]] && [[ "$(tr -d '[:space:]' < "${PID_FILE}")" == "$$" ]]; then
    rm -f "${PID_FILE}"
  fi
}

case "${MODE}" in
  navigation|mapping|ui-only) ;;
  *) fail "mode must be navigation, mapping, or ui-only" ;;
esac

for command_name in curl flock setsid timeout; do
  command -v "${command_name}" >/dev/null 2>&1 \
    || fail "required command is missing: ${command_name}"
done

[[ "${PORT}" =~ ^[0-9]+$ ]] || fail "invalid port: ${PORT}"
[[ "${MAP_NAME}" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "invalid map name: ${MAP_NAME}"
[[ -d "${MISSION_CONTROL_DIR}" ]] || fail "Mission Control directory not found: ${MISSION_CONTROL_DIR}"
[[ -x "${MISSION_CONTROL_DIR}/.venv-local/bin/python3" ]] \
  || fail "Mission Control environment is missing; run central/setup_wsl.sh first"

mkdir -p "${DATA_DIR}" "${STATE_DIR}" "${LOG_DIR}" "${MAP_DIR}"
if [[ -d "${SOURCE_MAP_DIR}" ]] && ! find "${MAP_DIR}" -maxdepth 1 -name '*.yaml' -print -quit | grep -q .; then
  cp -a "${SOURCE_MAP_DIR}/." "${MAP_DIR}/"
fi
if [[ ! -f "${DESTINATIONS_PATH}" ]]; then
  cp "${MISSION_CONTROL_DIR}/config/destinations.yaml" "${DESTINATIONS_PATH}"
fi

MAP_PATH="${MAP_DIR}/${MAP_NAME}.yaml"
if [[ "${MODE}" != "mapping" ]]; then
  [[ -f "${MAP_PATH}" ]] || fail "navigation map not found: ${MAP_PATH}"
fi

if [[ "${MODE}" == "navigation" && "${MAP_NAME}" == *_navigation ]]; then
  MAP_PROFILE="${MAP_NAME%_navigation}"
  KEEP_OUT_PATH="${MAP_DIR}/${MAP_PROFILE}_keepout.yaml"
  DISPLAY_PATH="${MAP_DIR}/${MAP_PROFILE}_display.yaml"
  [[ -f "${KEEP_OUT_PATH}" ]] || fail "keepout map not found: ${KEEP_OUT_PATH}"
  [[ -f "${DISPLAY_PATH}" ]] || fail "display map not found: ${DISPLAY_PATH}"
fi

exec 9>"${LOCK_FILE}"
flock -n 9 || fail "another central supervisor is already running"
if [[ -f "${PID_FILE}" ]]; then
  EXISTING_PID="$(tr -d '[:space:]' < "${PID_FILE}")"
  if [[ "${EXISTING_PID}" =~ ^[0-9]+$ ]] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
    fail "central supervisor is already running as PID ${EXISTING_PID}"
  fi
fi

BASE_URL="http://127.0.0.1:${PORT}"
if curl -fsS --max-time 1 "${BASE_URL}/health" >/dev/null 2>&1; then
  fail "port ${PORT} already has a Mission Control server"
fi
printf '%s\n' "$$" > "${PID_FILE}"

RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ROS_LOG="${LOG_DIR}/${RUN_STAMP}-${MODE}-ros.log"
SERVER_LOG="${LOG_DIR}/${RUN_STAMP}-${MODE}-mission-control.log"
trap shutdown_stack EXIT INT TERM HUP

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
export MISSION_CONTROL_DB_PATH="${DB_PATH}"
export MISSION_CONTROL_DESTINATIONS_PATH="${DESTINATIONS_PATH}"
export MISSION_CONTROL_ROBOT_ID="${MISSION_CONTROL_ROBOT_ID:-robot-1}"
export PORT
export HOST

if [[ "${MODE}" == "ui-only" ]]; then
  export MISSION_CONTROL_ROBOT_BACKEND=sim
  export MISSION_CONTROL_UI_MAP="${MAP_PATH}"
else
  [[ -f "${ROS_SETUP_FILE}" ]] || fail "ROS setup not found: ${ROS_SETUP_FILE}"
  [[ -f "${WORKSPACE_SETUP}" ]] || fail "workspace is not built: ${WORKSPACE_SETUP}"
  source_relaxed "${ROS_SETUP_FILE}"
  source_relaxed "${WORKSPACE_SETUP}"
  export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://${WORKSPACE}/install/my_bot/share/my_bot/config/cyclonedds.xml}"
  export MISSION_CONTROL_ROBOT_BACKEND=ros2
  export MISSION_CONTROL_ROS2_LAUNCHER_MODE=supervised
  export MISSION_CONTROL_ROS2_EXTERNAL_MAP_NAME="${MAP_NAME}"
  export MISSION_CONTROL_ROS2_CENTRAL_WORKSPACE="${WORKSPACE}"
  export MISSION_CONTROL_ROS2_MAP_DIRECTORY="${MAP_DIR}"

  ROS_ARGUMENTS=(
    ros2 launch my_bot central_compute.launch.py
    "use_rviz:=${RVIZ}"
  )
  if [[ "${MODE}" == "navigation" ]]; then
    ROS_ARGUMENTS+=(
      "use_slam:=false"
      "use_nav2:=true"
      "map:=${MAP_PATH}"
    )
    if [[ "${MAP_NAME}" == *_navigation ]]; then
      ROS_ARGUMENTS+=(
        "use_keepout:=true"
        "keepout_mask:=${KEEP_OUT_PATH}"
        "use_display_map:=true"
        "display_map:=${DISPLAY_PATH}"
      )
    else
      ROS_ARGUMENTS+=("use_keepout:=false" "use_display_map:=false")
    fi
  else
    ROS_ARGUMENTS+=("use_slam:=true" "use_nav2:=false")
  fi

  echo "Starting central ROS mode: ${MODE}"
  start_owned_process \
    ROS_PID \
    ROS_LAUNCHER_PID \
    "${ROS_CHILD_PID_FILE}" \
    "${WORKSPACE}" \
    "${ROS_LOG}" \
    "${ROS_ARGUMENTS[@]}"
fi

echo "Starting Mission Control..."
start_owned_process \
  SERVER_PID \
  SERVER_LAUNCHER_PID \
  "${SERVER_CHILD_PID_FILE}" \
  "${MISSION_CONTROL_DIR}" \
  "${SERVER_LOG}" \
  ./run_server.sh

deadline=$((SECONDS + 45))
until curl -fsS --max-time 1 "${BASE_URL}/health" >/dev/null 2>&1; do
  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    fail "Mission Control exited; inspect ${SERVER_LOG}"
  fi
  if [[ -n "${ROS_PID}" ]] && ! kill -0 "${ROS_PID}" 2>/dev/null; then
    fail "central ROS launch exited; inspect ${ROS_LOG}"
  fi
  (( SECONDS < deadline )) || fail "Mission Control did not become healthy within 45 seconds"
  sleep 0.25
done

echo
echo "IntelliTrolley central stack is running."
echo "Mode: ${MODE}"
echo "Dashboard: ${BASE_URL}/ui"
if [[ "${HOST}" != "127.0.0.1" && "${HOST}" != "localhost" ]]; then
  echo "Robot-LAN clients: http://<central-robot-lan-ip>:${PORT}/ui"
fi
echo "Mission Control log: ${SERVER_LOG}"
if [[ -n "${ROS_PID}" ]]; then
  echo "ROS log: ${ROS_LOG}"
fi
echo "Use central/stop_central_stack.sh for a controlled stop."

while kill -0 "${SERVER_PID}" 2>/dev/null; do
  if [[ -n "${ROS_PID}" ]] && ! kill -0 "${ROS_PID}" 2>/dev/null; then
    break
  fi
  sleep 0.5
done
