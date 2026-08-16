#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${PI_HOST:-zrpi-desktop.local}"
PI_PORT="${PI_PORT:-8000}"
UI_URL="http://${PI_HOST}:${PI_PORT}/ui"
HEALTH_URL="http://${PI_HOST}:${PI_PORT}/health"

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required to check the Pi Mission Control service." >&2
  exit 1
fi

if ! curl -fsS --connect-timeout 5 "${HEALTH_URL}" >/dev/null; then
  echo "Mission Control is not reachable at ${HEALTH_URL}." >&2
  echo "Check my-bot-mission-control.service on the Pi." >&2
  exit 1
fi

echo "Opening ${UI_URL}"
if command -v xdg-open >/dev/null 2>&1; then
  exec xdg-open "${UI_URL}"
fi

echo "Open this address in a browser:"
echo "  ${UI_URL}"
