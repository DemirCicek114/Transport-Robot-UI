#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export MISSION_CONTROL_UI_ONLY=true
exec "${SCRIPT_DIR}/launch_navigation_ui.sh" "$@"
