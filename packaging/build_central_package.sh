#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${REPOSITORY_ROOT}/.." && pwd)"
VERSION="${1:-$(tr -d '[:space:]' < "${SCRIPT_DIR}/VERSION")}"
PACKAGE_NAME="IntelliTrolley-Central-${VERSION}"
DIST_DIR="${REPOSITORY_ROOT}/dist"
STAGE_PARENT="$(mktemp -d)"
PACKAGE_ROOT="${STAGE_PARENT}/${PACKAGE_NAME}"
PAYLOAD_ROOT="${PACKAGE_ROOT}/payload"
PAYLOAD_WORKSPACE="${PAYLOAD_ROOT}/dev_ws"

cleanup() {
  rm -rf "${STAGE_PARENT}"
}
trap cleanup EXIT

[[ "${VERSION}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || {
    echo "Invalid package version: ${VERSION}" >&2
    exit 2
  }
for command_name in python3 rsync sha256sum zip; do
  command -v "${command_name}" >/dev/null 2>&1 \
    || {
      echo "Required packaging command is missing: ${command_name}" >&2
      exit 1
    }
done

mkdir -p \
  "${PACKAGE_ROOT}/windows" \
  "${PAYLOAD_ROOT}" \
  "${PAYLOAD_WORKSPACE}/robot_server" \
  "${PAYLOAD_WORKSPACE}/src" \
  "${DIST_DIR}"

cp "${SCRIPT_DIR}/README.package.md" "${PACKAGE_ROOT}/README.md"
printf '%s\n' "${VERSION}" > "${PACKAGE_ROOT}/PACKAGE-VERSION"
cp "${SCRIPT_DIR}/linux/install_payload.sh" "${PAYLOAD_ROOT}/install_payload.sh"
cp "${SCRIPT_DIR}/linux/intellitrolley-central" "${PAYLOAD_ROOT}/intellitrolley-central"
rsync -a "${REPOSITORY_ROOT}/windows/" "${PACKAGE_ROOT}/windows/"
rsync -a \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  "${REPOSITORY_ROOT}/central/" \
  "${PAYLOAD_WORKSPACE}/robot_server/central/"

MISSION_CONTROL_DEST="${PAYLOAD_WORKSPACE}/robot_server/mission_control_poc"
mkdir -p "${MISSION_CONTROL_DEST}"
for source_file in \
  README.md \
  app.py \
  requirements.txt \
  run_server.sh \
  setup_env_linux.sh; do
  cp \
    "${REPOSITORY_ROOT}/mission_control_poc/${source_file}" \
    "${MISSION_CONTROL_DEST}/${source_file}"
done
for source_dir in config mission_control ui; do
  rsync -a \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache/' \
    "${REPOSITORY_ROOT}/mission_control_poc/${source_dir}/" \
    "${MISSION_CONTROL_DEST}/${source_dir}/"
done

rsync -a \
  --exclude '.git/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'build/' \
  --exclude 'install/' \
  --exclude 'log/' \
  --exclude '*.xcf' \
  --exclude 'maps/xtramap1.pgm' \
  --exclude 'maps/xtramap1.yaml' \
  "${WORKSPACE_ROOT}/src/my_bot/" \
  "${PAYLOAD_WORKSPACE}/src/my_bot/"

chmod +x \
  "${PAYLOAD_ROOT}/install_payload.sh" \
  "${PAYLOAD_ROOT}/intellitrolley-central" \
  "${PAYLOAD_WORKSPACE}/robot_server/central/"*.sh \
  "${PAYLOAD_WORKSPACE}/robot_server/mission_control_poc/"*.sh

if find "${PACKAGE_ROOT}" \
  \( -name '.git' -o -name '__pycache__' -o -name '.pytest_cache' \
     -o -name '.venv*' -o -name '*.pyc' -o -name '*.sqlite3' \) \
  -print -quit | grep -q .; then
  echo "Package staging contains a forbidden generated or mutable file." >&2
  exit 1
fi

python3 - "${PACKAGE_ROOT}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
entries = []
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.name == "release-manifest.json":
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    entries.append({
        "path": path.relative_to(root).as_posix(),
        "sha256": digest,
        "size": path.stat().st_size,
    })
(root / "release-manifest.json").write_text(
    json.dumps({"algorithm": "SHA-256", "files": entries}, indent=2) + "\n",
    encoding="utf-8",
)
PY

ARCHIVE_PATH="${DIST_DIR}/${PACKAGE_NAME}.zip"
rm -f "${ARCHIVE_PATH}" "${ARCHIVE_PATH}.sha256"
(
  cd "${STAGE_PARENT}"
  zip -q -r -X "${ARCHIVE_PATH}" "${PACKAGE_NAME}"
)
(
  cd "${DIST_DIR}"
  sha256sum "${PACKAGE_NAME}.zip" > "${PACKAGE_NAME}.zip.sha256"
)

echo "Built ${ARCHIVE_PATH}"
echo "Checksum: ${ARCHIVE_PATH}.sha256"
