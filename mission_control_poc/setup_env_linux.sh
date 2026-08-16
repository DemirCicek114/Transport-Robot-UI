#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv-local"
PYTHON_BIN="/usr/bin/python3"
VENV_PYTHON="${VENV_DIR}/bin/python3"
PYTHON_VERSION="$("${PYTHON_BIN}" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "System Python not found at ${PYTHON_BIN}."
  exit 1
fi

if ! "${PYTHON_BIN}" - <<'PY' >/dev/null 2>&1
import ensurepip
PY
then
  cat <<EOF
System Python is missing ensurepip / venv support.

Run this once:
  sudo apt install python${PYTHON_VERSION}-venv

If that package is unavailable, use:
  sudo apt install python3-venv

Then rerun:
  ./setup_env_linux.sh
EOF
  exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  "${PYTHON_BIN}" -m venv --system-site-packages "${VENV_DIR}"
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "Virtualenv Python not found at ${VENV_PYTHON}."
  echo "Remove the stale .venv-local directory and rerun ./setup_env_linux.sh."
  exit 1
fi

if [[ -f "${VENV_DIR}/pyvenv.cfg" ]]; then
  "${PYTHON_BIN}" - "${VENV_DIR}/pyvenv.cfg" <<'PY'
from pathlib import Path
import sys

cfg_path = Path(sys.argv[1])
text = cfg_path.read_text()
needle = "include-system-site-packages = false"
if needle in text:
    cfg_path.write_text(text.replace(needle, "include-system-site-packages = true", 1))
PY
fi

"${VENV_PYTHON}" -m pip install --upgrade pip
"${VENV_PYTHON}" -m pip install -r "${SCRIPT_DIR}/requirements.txt"

cat <<EOF
Environment ready.

Activate it with:
  source "${VENV_DIR}/bin/activate"

Or just run:
  ./run_server.sh
EOF
