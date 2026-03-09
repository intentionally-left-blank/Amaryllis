#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${PROJECT_DIR}/dist"
APP_BUNDLE="${DIST_DIR}/Amaryllis.app"
DMG_FILE="${DIST_DIR}/Amaryllis.dmg"
STAGE_DIR="${DIST_DIR}/dmg-stage"

"${SCRIPT_DIR}/build_app.sh"

rm -rf "${STAGE_DIR}"
mkdir -p "${STAGE_DIR}"
cp -R "${APP_BUNDLE}" "${STAGE_DIR}/Amaryllis.app"

rm -f "${DMG_FILE}"
hdiutil create \
  -volname "Amaryllis" \
  -srcfolder "${STAGE_DIR}" \
  -ov -format UDZO \
  "${DMG_FILE}" >/dev/null

rm -rf "${STAGE_DIR}"

echo "Built dmg: ${DMG_FILE}"
