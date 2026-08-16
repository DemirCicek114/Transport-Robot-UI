#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: install_payload.sh <payload-directory> <version>" >&2
  exit 2
fi

PAYLOAD_DIR="$(cd "$1" && pwd)"
VERSION="$2"
APP_ROOT="${HOME}/.local/opt/intellitrolley"
RELEASES_DIR="${APP_ROOT}/releases"
RELEASE_DIR="${RELEASES_DIR}/${VERSION}"
BACKUP_DIR="${RELEASES_DIR}/${VERSION}.previous"
SOURCE_WORKSPACE="${PAYLOAD_DIR}/dev_ws"

[[ "${VERSION}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || {
    echo "Invalid package version: ${VERSION}" >&2
    exit 2
  }
[[ -f "${SOURCE_WORKSPACE}/robot_server/central/setup_wsl.sh" ]] \
  || {
    echo "The package payload is incomplete: central/setup_wsl.sh is missing." >&2
    exit 1
  }
[[ -f "${SOURCE_WORKSPACE}/src/my_bot/package.xml" ]] \
  || {
    echo "The package payload is incomplete: src/my_bot/package.xml is missing." >&2
    exit 1
  }

mkdir -p "${RELEASES_DIR}"
rm -rf "${BACKUP_DIR}"
if [[ -e "${RELEASE_DIR}" ]]; then
  mv "${RELEASE_DIR}" "${BACKUP_DIR}"
fi

restore_previous_release() {
  local exit_code=$?
  if (( exit_code == 0 )); then
    return
  fi
  trap - EXIT
  echo "Installation failed; restoring the previous ${VERSION} release." >&2
  rm -rf "${RELEASE_DIR}"
  if [[ -e "${BACKUP_DIR}" ]]; then
    mv "${BACKUP_DIR}" "${RELEASE_DIR}"
  fi
  exit "${exit_code}"
}
trap restore_previous_release EXIT

mkdir -p "${RELEASE_DIR}"
cp -a "${SOURCE_WORKSPACE}/." "${RELEASE_DIR}/dev_ws/"
chmod +x \
  "${RELEASE_DIR}/dev_ws/robot_server/central/"*.sh \
  "${RELEASE_DIR}/dev_ws/robot_server/mission_control_poc/"*.sh

INTELLITROLLEY_WORKSPACE="${RELEASE_DIR}/dev_ws" \
  "${RELEASE_DIR}/dev_ws/robot_server/central/setup_wsl.sh"

printf '%s\n' "${VERSION}" > "${RELEASE_DIR}/PACKAGE-VERSION"
ln -sfn "${RELEASE_DIR}" "${APP_ROOT}/current"
sudo install -m 0755 \
  "${PAYLOAD_DIR}/intellitrolley-central" \
  /usr/local/bin/intellitrolley-central
rm -rf "${BACKUP_DIR}"
trap - EXIT

echo
echo "Installed IntelliTrolley Central ${VERSION}."
echo "Application: ${RELEASE_DIR}"
echo "Mutable data: ${HOME}/.local/share/intellitrolley"
