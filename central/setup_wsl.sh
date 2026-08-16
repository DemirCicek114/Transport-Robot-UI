#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_WORKSPACE="$(cd "${REPOSITORY_ROOT}/.." && pwd)"
WORKSPACE="${INTELLITROLLEY_WORKSPACE:-${DEFAULT_WORKSPACE}}"
MISSION_CONTROL_DIR="${REPOSITORY_ROOT}/mission_control_poc"
DATA_DIR="${INTELLITROLLEY_DATA_DIR:-${HOME}/.local/share/intellitrolley}"
CONFIG_DIR="${HOME}/.config/intellitrolley"
CONFIG_FILE="${CONFIG_DIR}/central.env"
ROS_KEY_URL="https://raw.githubusercontent.com/ros/rosdistro/master/ros.key"
ROS_KEY_SHA256="4a91c49af0d6f0016108b93698782b596c27ccd836937e18e0e36c3347dc602f"
ROS_KEY_PATH="/usr/share/keyrings/ros-archive-keyring.gpg"
ROS_SOURCE_PATH="/etc/apt/sources.list.d/ros2.list"

download_verified() {
  local url="$1"
  local expected_sha256="$2"
  local destination="$3"

  curl --fail --location --silent --show-error "${url}" --output "${destination}"
  echo "${expected_sha256}  ${destination}" | sha256sum --check --status \
    || {
      echo "Downloaded file failed its SHA-256 check: ${url}" >&2
      return 1
    }
}

if [[ ! -r /etc/os-release ]] || ! grep -q 'VERSION_ID="22.04"' /etc/os-release; then
  echo "IntelliTrolley requires Ubuntu 22.04 under WSL 2 for ROS 2 Humble." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y \
  ca-certificates \
  curl \
  python3-venv \
  software-properties-common \
  util-linux

if [[ ! -f /opt/ros/humble/setup.bash ]]; then
  echo "Installing the pinned ROS 2 Humble apt repository..."
  sudo add-apt-repository -y universe
  ROS_KEY_TEMP="$(mktemp)"
  trap 'rm -f "${ROS_KEY_TEMP:-}"' EXIT
  download_verified "${ROS_KEY_URL}" "${ROS_KEY_SHA256}" "${ROS_KEY_TEMP}"
  sudo install -D -m 0644 "${ROS_KEY_TEMP}" "${ROS_KEY_PATH}"
  # packages.ros.org is an OSUOSL mirror alias whose HTTPS certificate does
  # not consistently cover that hostname. APT still verifies the signed
  # repository metadata against the pinned ROS key below.
  printf 'deb [arch=%s signed-by=%s] http://packages.ros.org/ros2/ubuntu jammy main\n' \
    "$(dpkg --print-architecture)" \
    "${ROS_KEY_PATH}" \
    | sudo tee "${ROS_SOURCE_PATH}" >/dev/null
  sudo apt-get update
fi

sudo apt-get install -y \
  python3-colcon-common-extensions \
  ros-dev-tools \
  ros-humble-desktop \
  ros-humble-navigation2 \
  ros-humble-nav2-bringup \
  ros-humble-rmw-cyclonedds-cpp \
  ros-humble-slam-toolbox

mkdir -p "${CONFIG_DIR}" "${DATA_DIR}/logs" "${DATA_DIR}/maps"
if [[ ! -f "${CONFIG_FILE}" ]]; then
  cp "${SCRIPT_DIR}/central.env.example" "${CONFIG_FILE}"
fi
if [[ ! -f "${DATA_DIR}/destinations.yaml" ]]; then
  cp "${MISSION_CONTROL_DIR}/config/destinations.yaml" "${DATA_DIR}/destinations.yaml"
fi
if ! find "${DATA_DIR}/maps" -maxdepth 1 -name '*.yaml' -print -quit | grep -q .; then
  cp -a "${WORKSPACE}/src/my_bot/maps/." "${DATA_DIR}/maps/"
fi

set +u
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
set -u
(
  cd "${WORKSPACE}"
  colcon build --symlink-install --packages-select my_bot
)
(
  cd "${MISSION_CONTROL_DIR}"
  ./setup_env_linux.sh
)

echo
echo "WSL central environment is ready."
echo "Review: ${CONFIG_FILE}"
echo "Test: ${SCRIPT_DIR}/central_doctor.sh"
echo "Start: ${SCRIPT_DIR}/start_central_stack.sh ui-only"
