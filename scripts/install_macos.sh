#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${AMARYLLIS_REPO_URL:-https://github.com/apothecary-94/Amaryllis.git}"
BRANCH="${AMARYLLIS_BRANCH:-main}"
APP_NAME="Amaryllis.app"
BUNDLE_ID="org.amaryllis.app"

SUPPORT_ROOT="${HOME}/Library/Application Support/amaryllis"
RUNTIME_SOURCE_DIR="${SUPPORT_ROOT}/runtime-src"
RUNTIME_VENV="${RUNTIME_SOURCE_DIR}/.venv"

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

mkdir -p "${SUPPORT_ROOT}"
mkdir -p "${RUNTIME_SOURCE_DIR}"

echo "Syncing runtime source to ${RUNTIME_SOURCE_DIR}"
rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.build' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude 'macos/AmaryllisApp/.build' \
  --exclude 'macos/AmaryllisApp/dist' \
  "${SOURCE_DIR}/" "${RUNTIME_SOURCE_DIR}/"

echo "Building app bundle"
cd "${SOURCE_DIR}/macos/AmaryllisApp"
./scripts/build_app.sh

APP_PATH="${SOURCE_DIR}/macos/AmaryllisApp/dist/${APP_NAME}"
if [[ ! -d "${APP_PATH}" ]]; then
  echo "Build failed: ${APP_PATH} not found"
  exit 1
fi

echo "Preparing Python environment at ${RUNTIME_VENV}"
python3 -m venv "${RUNTIME_VENV}"
"${RUNTIME_VENV}/bin/python" -m pip install --upgrade pip
"${RUNTIME_VENV}/bin/python" -m pip install -r "${RUNTIME_SOURCE_DIR}/requirements.txt"

TARGET_DIR="/Applications"
if [[ ! -w "${TARGET_DIR}" ]]; then
  TARGET_DIR="$HOME/Applications"
  mkdir -p "${TARGET_DIR}"
fi

echo "Installing app to ${TARGET_DIR}"
rm -rf "${TARGET_DIR}/${APP_NAME}"
cp -R "${APP_PATH}" "${TARGET_DIR}/${APP_NAME}"

echo "Saving runtime defaults for ${BUNDLE_ID}"
defaults write "${BUNDLE_ID}" amaryllis.runtimeDirectory "${RUNTIME_SOURCE_DIR}"
defaults write "${BUNDLE_ID}" amaryllis.endpoint "http://localhost:8000"

echo "Installed: ${TARGET_DIR}/${APP_NAME}"
open "${TARGET_DIR}/${APP_NAME}"
