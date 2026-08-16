#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_WORKSPACE="$(cd "${REPOSITORY_ROOT}/.." && pwd)"
CONFIG_FILE="${INTELLITROLLEY_CONFIG_FILE:-${HOME}/.config/intellitrolley/central.env}"
if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

WORKSPACE="${INTELLITROLLEY_PACKAGED_WORKSPACE:-${INTELLITROLLEY_WORKSPACE:-${DEFAULT_WORKSPACE}}}"
DATA_DIR="${INTELLITROLLEY_DATA_DIR:-${HOME}/.local/share/intellitrolley}"
MAP_DIR="${INTELLITROLLEY_MAP_DIR:-${DATA_DIR}/maps}"
MAP_NAME="${INTELLITROLLEY_MAP_NAME:-atrium_navigation}"
MAP_NAME="${MAP_NAME%.yaml}"
PORT="${INTELLITROLLEY_PORT:-8000}"
ROS_SETUP_FILE="${ROS_SETUP_FILE:-/opt/ros/humble/setup.bash}"
FAILURES=0

pass() { echo "PASS  $*"; }
warn() { echo "WARN  $*"; }
fail() { echo "FAIL  $*"; FAILURES=$((FAILURES + 1)); }

if [[ -r /etc/os-release ]] && grep -q 'VERSION_ID="22.04"' /etc/os-release; then
  pass "Ubuntu 22.04"
else
  fail "expected Ubuntu 22.04 for ROS 2 Humble"
fi

[[ -f "${ROS_SETUP_FILE}" ]] && pass "ROS 2 Humble setup" || fail "missing ${ROS_SETUP_FILE}"
[[ -f "${WORKSPACE}/install/setup.bash" ]] \
  && pass "central workspace build" \
  || fail "workspace is not built: ${WORKSPACE}"
[[ -x "${REPOSITORY_ROOT}/mission_control_poc/.venv-local/bin/python3" ]] \
  && pass "Mission Control Python environment" \
  || fail "Mission Control Python environment is missing"
[[ -f "${MAP_DIR}/${MAP_NAME}.yaml" ]] \
  && pass "navigation map ${MAP_NAME}" \
  || fail "missing map ${MAP_DIR}/${MAP_NAME}.yaml"

if [[ "${MAP_NAME}" == *_navigation ]]; then
  MAP_PROFILE="${MAP_NAME%_navigation}"
  for layer in keepout display; do
    [[ -f "${MAP_DIR}/${MAP_PROFILE}_${layer}.yaml" ]] \
      && pass "${layer} map layer" \
      || fail "missing ${MAP_PROFILE}_${layer}.yaml"
  done
fi

if curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  pass "Mission Control health endpoint"
else
  warn "Mission Control is not currently running on port ${PORT}"
fi

if [[ -f "${ROS_SETUP_FILE}" && -f "${WORKSPACE}/install/setup.bash" ]]; then
  set +u
  # shellcheck disable=SC1090
  source "${ROS_SETUP_FILE}"
  # shellcheck disable=SC1090
  source "${WORKSPACE}/install/setup.bash"
  set -u
  export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
  export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
  export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
  export CYCLONEDDS_URI="${CYCLONEDDS_URI:-file://${WORKSPACE}/install/my_bot/share/my_bot/config/cyclonedds.xml}"

  timeout --signal=TERM --kill-after=1s 3s \
    ros2 daemon stop >/dev/null 2>&1 || true
  TOPICS="$(
    timeout --signal=INT --kill-after=2s 8s \
      ros2 topic list --no-daemon --spin-time 5.0 2>/dev/null || true
  )"
  ACTIONS="$(
    timeout --signal=INT --kill-after=2s 8s \
      ros2 action list 2>/dev/null || true
  )"
  for topic_name in \
    /scan_filtered \
    /diff_cont/odom \
    /robot_health/ready \
    /robot_health/hardware_healthy \
    /robot_health/lidar_healthy \
    /robot_health/odometry_healthy \
    /robot_health/controller_healthy \
    /robot_health/obstacle_health \
    /robot_health/startup_gate_open \
    /battery_state; do
    if grep -Fxq "${topic_name}" <<<"${TOPICS}"; then
      pass "ROS topic ${topic_name}"
    else
      warn "ROS topic not discovered: ${topic_name}"
    fi
  done
  if grep -Fxq /navigate_to_pose <<<"${ACTIONS}"; then
    pass "NavigateToPose action"
  else
    warn "NavigateToPose action is not currently available"
  fi

  timeout --signal=INT --kill-after=1s 3s \
    ros2 run tf2_ros tf2_echo odom base_link >/dev/null 2>&1 \
    && pass "TF odom → base_link" \
    || warn "TF odom → base_link is not currently available"
  timeout --signal=INT --kill-after=1s 3s \
    ros2 run tf2_ros tf2_echo map odom >/dev/null 2>&1 \
    && pass "TF map → odom" \
    || warn "TF map → odom is not currently available"
fi

if (( FAILURES > 0 )); then
  echo
  echo "Doctor found ${FAILURES} installation problem(s)."
  exit 1
fi

echo
echo "Installation checks passed. WARN items may be expected while the Pi or navigation mode is offline."
