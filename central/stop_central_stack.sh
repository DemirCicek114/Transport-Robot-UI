#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="${INTELLITROLLEY_CONFIG_FILE:-${HOME}/.config/intellitrolley/central.env}"
if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

STATE_DIR="${INTELLITROLLEY_STATE_DIR:-${HOME}/.local/state/intellitrolley}"
PID_FILE="${STATE_DIR}/central.pid"

if [[ ! -f "${PID_FILE}" ]]; then
  echo "IntelliTrolley central stack is not running."
  exit 0
fi

SUPERVISOR_PID="$(tr -d '[:space:]' < "${PID_FILE}")"
if [[ ! "${SUPERVISOR_PID}" =~ ^[0-9]+$ ]]; then
  echo "Invalid central PID file: ${PID_FILE}" >&2
  exit 1
fi
if ! kill -0 "${SUPERVISOR_PID}" 2>/dev/null; then
  rm -f "${PID_FILE}"
  echo "Removed stale central PID file."
  exit 0
fi

echo "Requesting controlled stop from PID ${SUPERVISOR_PID}..."
kill -INT "${SUPERVISOR_PID}"
for _attempt in $(seq 1 200); do
  if ! kill -0 "${SUPERVISOR_PID}" 2>/dev/null; then
    echo "IntelliTrolley central stack stopped."
    exit 0
  fi
  sleep 0.1
done

echo "Central stack did not stop within 20 seconds; inspect its logs before retrying." >&2
exit 1
