#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VERSION="${1:-$(tr -d '[:space:]' < "${SCRIPT_DIR}/VERSION")}"
PACKAGE_NAME="IntelliTrolley-Central-${VERSION}"
SETUP_NAME="IntelliTrolley-Setup-${VERSION}.exe"
INSTALL_GUIDE_NAME="IntelliTrolley-Windows-Installation-${VERSION}.md"
DIST_DIR="${REPOSITORY_ROOT}/dist"
BUILD_ROOT="$(mktemp -d)"
NSIS_SOURCE_DIR="${SCRIPT_DIR}/windows"
MAKENSIS_BIN="${NSIS_MAKENSIS:-$(command -v makensis || true)}"
NSIS_DATA_DIR="${NSIS_HOME:-/usr/share/nsis}"

cleanup() {
  rm -rf "${BUILD_ROOT}"
}
trap cleanup EXIT

[[ "${VERSION}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || {
    echo "Invalid package version: ${VERSION}" >&2
    exit 2
  }
[[ -n "${MAKENSIS_BIN}" && -x "${MAKENSIS_BIN}" ]] \
  || {
    echo "makensis is required. Install the NSIS compiler or set NSIS_MAKENSIS." >&2
    exit 1
  }
[[ -d "${NSIS_DATA_DIR}/Stubs" && -d "${NSIS_DATA_DIR}/Include" ]] \
  || {
    echo "NSIS data files were not found at ${NSIS_DATA_DIR}. Set NSIS_HOME." >&2
    exit 1
  }
for command_name in objdump sha256sum unzip; do
  command -v "${command_name}" >/dev/null 2>&1 \
    || {
      echo "Required installer build command is missing: ${command_name}" >&2
      exit 1
    }
done

"${SCRIPT_DIR}/build_central_package.sh" "${VERSION}"
unzip -q "${DIST_DIR}/${PACKAGE_NAME}.zip" -d "${BUILD_ROOT}"
PACKAGE_ROOT="${BUILD_ROOT}/${PACKAGE_NAME}"
LAUNCHER_PATH="${BUILD_ROOT}/IntelliTrolley-Central.exe"
SETUP_PATH="${DIST_DIR}/${SETUP_NAME}"

NSISDIR="${NSIS_DATA_DIR}" "${MAKENSIS_BIN}" -V2 \
  "-DVERSION=${VERSION}" \
  "-DOUTPUT_FILE=${LAUNCHER_PATH}" \
  "${NSIS_SOURCE_DIR}/IntelliTrolleyLauncher.nsi"

rm -f "${SETUP_PATH}" "${SETUP_PATH}.sha256"
NSISDIR="${NSIS_DATA_DIR}" "${MAKENSIS_BIN}" -V2 \
  "-DVERSION=${VERSION}" \
  "-DOUTPUT_FILE=${SETUP_PATH}" \
  "-DPACKAGE_ROOT=${PACKAGE_ROOT}" \
  "-DLAUNCHER_EXE=${LAUNCHER_PATH}" \
  "${NSIS_SOURCE_DIR}/IntelliTrolleySetup.nsi"

objdump -f "${SETUP_PATH}" | grep -q "pei-x86-64" \
  || {
    echo "Built setup is not a 64-bit Windows PE executable." >&2
    exit 1
  }
objdump -f "${LAUNCHER_PATH}" | grep -q "pei-x86-64" \
  || {
    echo "Built launcher is not a 64-bit Windows PE executable." >&2
    exit 1
  }

(
  cd "${DIST_DIR}"
  sha256sum "${SETUP_NAME}" > "${SETUP_NAME}.sha256"
)
cp "${REPOSITORY_ROOT}/windows/README.md" \
  "${DIST_DIR}/${INSTALL_GUIDE_NAME}"

echo "Built ${SETUP_PATH}"
echo "Checksum: ${SETUP_PATH}.sha256"
echo "Instructions: ${DIST_DIR}/${INSTALL_GUIDE_NAME}"
echo "The setup is unsigned and must still be acceptance-tested on Windows."
