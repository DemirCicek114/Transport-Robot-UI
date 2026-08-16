#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${MISSION_CONTROL_SERVICE_USER:-$(id -un)}"
SERVICE_NAME="my-bot-mission-control.service"
TEMPLATE_PATH="${SCRIPT_DIR}/systemd/${SERVICE_NAME}.in"
SYSTEMD_PATH="/etc/systemd/system/${SERVICE_NAME}"

if [[ "${SERVICE_USER}" == "root" ]]; then
  cat >&2 <<'EOF'
Run this installer as the normal robot user, not with sudo.
The installer requests sudo only for the systemd installation steps.
EOF
  exit 1
fi

SERVICE_HOME="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
if [[ -z "${SERVICE_HOME}" ]]; then
  echo "Could not determine the home directory for ${SERVICE_USER}." >&2
  exit 1
fi

ROBOT_WORKSPACE="${ROBOT_WORKSPACE:-${SERVICE_HOME}/robot_ws}"
STATE_DIRECTORY="${MISSION_CONTROL_STATE_DIRECTORY:-${SERVICE_HOME}/.local/share/my-bot}"
MAP_NAME="${MISSION_CONTROL_DEFAULT_MAP:-atrium_navigation}"
MAP_NAME="${MAP_NAME%.yaml}"
if [[ "${MAP_NAME}" != *_navigation ]]; then
  echo "Pi autonomy requires a layered map name ending in _navigation." >&2
  exit 1
fi

if [[ ! -f "${ROBOT_WORKSPACE}/install/setup.bash" ]]; then
  echo "Robot workspace is not built: ${ROBOT_WORKSPACE}/install/setup.bash is missing." >&2
  exit 1
fi
if [[ ! -x "${SCRIPT_DIR}/.venv-local/bin/python3" ]]; then
  cat >&2 <<EOF
Mission Control's Python environment is missing.
Run this first:
  ${SCRIPT_DIR}/setup_env_linux.sh
EOF
  exit 1
fi
if [[ ! -f "${TEMPLATE_PATH}" ]]; then
  echo "Systemd template not found: ${TEMPLATE_PATH}" >&2
  exit 1
fi

if [[ -n "${MISSION_CONTROL_ROS2_MAP_DIRECTORY:-}" ]]; then
  MAP_DIRECTORY="${MISSION_CONTROL_ROS2_MAP_DIRECTORY}"
elif [[ -d "${ROBOT_WORKSPACE}/src/catering_bot/maps" ]]; then
  MAP_DIRECTORY="${ROBOT_WORKSPACE}/src/catering_bot/maps"
elif [[ -d "${ROBOT_WORKSPACE}/src/my_bot/maps" ]]; then
  MAP_DIRECTORY="${ROBOT_WORKSPACE}/src/my_bot/maps"
else
  MAP_DIRECTORY="${ROBOT_WORKSPACE}/install/my_bot/share/my_bot/maps"
fi

for layer in navigation keepout display; do
  layer_path="${MAP_DIRECTORY}/${MAP_NAME%_navigation}_${layer}.yaml"
  if [[ ! -f "${layer_path}" ]]; then
    echo "Required Atrium map layer is missing: ${layer_path}" >&2
    exit 1
  fi
done

mkdir -p "${STATE_DIRECTORY}"
if [[ ! -f "${STATE_DIRECTORY}/destinations.yaml" ]]; then
  cp "${SCRIPT_DIR}/config/destinations.yaml" "${STATE_DIRECTORY}/destinations.yaml"
fi
if [[ ! -f "${STATE_DIRECTORY}/mission_control.sqlite3" ]] \
    && [[ -f "${SCRIPT_DIR}/mission_control.sqlite3" ]]; then
  cp "${SCRIPT_DIR}/mission_control.sqlite3" "${STATE_DIRECTORY}/mission_control.sqlite3"
fi

tmp_unit="$(mktemp)"
trap 'rm -f "${tmp_unit}"' EXIT

sed \
  -e "s|@SERVICE_USER@|${SERVICE_USER}|g" \
  -e "s|@SERVICE_HOME@|${SERVICE_HOME}|g" \
  -e "s|@ROBOT_WORKSPACE@|${ROBOT_WORKSPACE}|g" \
  -e "s|@MISSION_CONTROL_DIR@|${SCRIPT_DIR}|g" \
  -e "s|@STATE_DIRECTORY@|${STATE_DIRECTORY}|g" \
  -e "s|@MAP_DIRECTORY@|${MAP_DIRECTORY}|g" \
  -e "s|@MAP_NAME@|${MAP_NAME}|g" \
  "${TEMPLATE_PATH}" >"${tmp_unit}"

echo "Installing ${SERVICE_NAME} for ${SERVICE_USER}..."
sudo install -m 0644 "${tmp_unit}" "${SYSTEMD_PATH}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

cat <<EOF

Mission Control is installed on the Pi.

Open from the laptop:
  http://$(hostname).local:8000/ui

Status:
  sudo systemctl status ${SERVICE_NAME} --no-pager

Logs:
  sudo journalctl -u ${SERVICE_NAME} -f -o cat
EOF
