#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${AMARYLLIS_REPO_URL:-https://github.com/intentionally-left-blank/Amaryllis.git}"
BRANCH="${AMARYLLIS_BRANCH:-main}"
APP_NAME="Amaryllis.app"

WORKDIR=""
SOURCE_DIR="$(pwd)"

cleanup() {
  if [[ -n "${WORKDIR}" && -d "${WORKDIR}" ]]; then
    rm -rf "${WORKDIR}"
  fi
}
trap cleanup EXIT

if [[ ! -x "${SOURCE_DIR}/macos/AmaryllisApp/scripts/build_app.sh" ]]; then
  WORKDIR="$(mktemp -d)"
  SOURCE_DIR="${WORKDIR}/Amaryllis"
  git clone --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${SOURCE_DIR}"
fi

cd "${SOURCE_DIR}/macos/AmaryllisApp"
./scripts/build_app.sh

APP_PATH="${SOURCE_DIR}/macos/AmaryllisApp/dist/${APP_NAME}"
if [[ ! -d "${APP_PATH}" ]]; then
  echo "Build failed: ${APP_PATH} not found"
  exit 1
fi

TARGET_DIR="/Applications"
if [[ ! -w "${TARGET_DIR}" ]]; then
  TARGET_DIR="$HOME/Applications"
  mkdir -p "${TARGET_DIR}"
fi

rm -rf "${TARGET_DIR}/${APP_NAME}"
cp -R "${APP_PATH}" "${TARGET_DIR}/${APP_NAME}"

echo "Installed: ${TARGET_DIR}/${APP_NAME}"
open "${TARGET_DIR}/${APP_NAME}"
