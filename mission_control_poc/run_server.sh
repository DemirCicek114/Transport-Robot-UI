#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv-local"
VENV_PYTHON="${VENV_DIR}/bin/python3"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
BACKEND="${MISSION_CONTROL_ROBOT_BACKEND:-sim}"

source_relaxed() {
  set +u
  # shellcheck disable=SC1090
  source "$1"
  set -u
}

if [[ ! -f "${VENV_DIR}/bin/activate" ]]; then
  cat <<'EOF'
Local Linux virtualenv is missing.

Run:
  ./setup_env_linux.sh
EOF
  exit 1
fi

if [[ "${BACKEND}" == "ros2" ]]; then
  export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
  if [[ -n "${ROS_SETUP_FILE:-}" && -f "${ROS_SETUP_FILE}" ]]; then
    source_relaxed "${ROS_SETUP_FILE}"
  elif [[ -n "${ROS_DISTRO:-}" && -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]]; then
    source_relaxed "/opt/ros/${ROS_DISTRO}/setup.bash"
  elif [[ -f /opt/ros/jazzy/setup.bash ]]; then
    source_relaxed /opt/ros/jazzy/setup.bash
  else
    cat <<'EOF'
ROS 2 backend requested, but no ROS setup file was found.

Set one of:
  export ROS_SETUP_FILE=/opt/ros/<distro>/setup.bash
or:
  source /opt/ros/<distro>/setup.bash

Then rerun:
  ./run_server.sh
EOF
    exit 1
  fi
fi

source_relaxed "${VENV_DIR}/bin/activate"
exec "${VENV_PYTHON}" -m uvicorn app:app --host "${HOST}" --port "${PORT}"
