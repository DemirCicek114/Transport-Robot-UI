#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_WORKSPACE="$(cd "${REPOSITORY_ROOT}/.." && pwd)"
CONFIG_DIR="${HOME}/.config/intellitrolley"
CONFIG_FILE="${INTELLITROLLEY_CONFIG_FILE:-${CONFIG_DIR}/central.env}"
WORKSPACE="${INTELLITROLLEY_PACKAGED_WORKSPACE:-${INTELLITROLLEY_WORKSPACE:-${DEFAULT_WORKSPACE}}}"
ROBOT_ADDRESS="${1:-}"
ROS_DOMAIN="${2:-}"

usage() {
  echo "Usage: configure_central_network.sh <robot-ipv4-address> <ros-domain-id>" >&2
  exit 2
}

[[ $# -eq 2 ]] || usage
python3 - "${ROBOT_ADDRESS}" "${ROS_DOMAIN}" <<'PY'
import ipaddress
import sys

try:
    address = ipaddress.ip_address(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"Invalid robot address: {exc}")
if address.version != 4 or not address.is_private:
    raise SystemExit("Robot address must be a private IPv4 address.")

try:
    domain = int(sys.argv[2], 10)
except ValueError:
    raise SystemExit("ROS domain ID must be an integer.")
if not 0 <= domain <= 232:
    raise SystemExit("ROS domain ID must be between 0 and 232.")
PY

BASE_CONFIG="${WORKSPACE}/install/my_bot/share/my_bot/config/cyclonedds.xml"
GENERATOR="${WORKSPACE}/install/my_bot/lib/my_bot/generate_cyclonedds_config.py"
GENERATED_CONFIG="${CONFIG_DIR}/cyclonedds.xml"

[[ -f "${BASE_CONFIG}" ]] || {
  echo "Cyclone DDS base configuration is missing: ${BASE_CONFIG}" >&2
  exit 1
}
[[ -f "${GENERATOR}" ]] || {
  echo "Cyclone DDS configuration generator is missing: ${GENERATOR}" >&2
  exit 1
}

mkdir -p "${CONFIG_DIR}"
if [[ ! -f "${CONFIG_FILE}" ]]; then
  cp "${SCRIPT_DIR}/central.env.example" "${CONFIG_FILE}"
fi

python3 "${GENERATOR}" \
  --base "${BASE_CONFIG}" \
  --output "${GENERATED_CONFIG}" \
  --peers "${ROBOT_ADDRESS}" \
  --allow-multicast spdp

update_setting() {
  local key="$1"
  local value="$2"
  local temporary_file
  temporary_file="$(mktemp "${CONFIG_DIR}/central.env.XXXXXX")"
  awk -v key="${key}" -v replacement="${key}=${value}" '
    BEGIN { replaced = 0 }
    $0 ~ "^[[:space:]]*(export[[:space:]]+)?" key "=" {
      if (!replaced) {
        print replacement
        replaced = 1
      }
      next
    }
    { print }
    END {
      if (!replaced) {
        print replacement
      }
    }
  ' "${CONFIG_FILE}" >"${temporary_file}"
  chmod 0600 "${temporary_file}"
  mv "${temporary_file}" "${CONFIG_FILE}"
}

update_setting ROS_DOMAIN_ID "${ROS_DOMAIN}"
update_setting ROS_LOCALHOST_ONLY 0
update_setting RMW_IMPLEMENTATION rmw_cyclonedds_cpp
update_setting CYCLONEDDS_URI "file://${GENERATED_CONFIG}"

echo
echo "Configured IntelliTrolley central networking:"
echo "  robot peer: ${ROBOT_ADDRESS}"
echo "  ROS domain: ${ROS_DOMAIN}"
echo "  Cyclone DDS: ${GENERATED_CONFIG}"
