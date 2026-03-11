#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${PROJECT_DIR}/dist"
APP_NAME="Amaryllis"
APP_BUNDLE="${DIST_DIR}/${APP_NAME}.app"
ASSETS_DIR="${PROJECT_DIR}/Assets"
ICON_FILE="${ASSETS_DIR}/AppIcon.icns"

"${SCRIPT_DIR}/generate_icon.sh"

swift build --configuration release --package-path "${PROJECT_DIR}"

CANDIDATES=(
  "${PROJECT_DIR}/.build/release/AmaryllisApp"
  "${PROJECT_DIR}/.build/arm64-apple-macosx/release/AmaryllisApp"
  "${PROJECT_DIR}/.build/x86_64-apple-macosx/release/AmaryllisApp"
)

BINARY=""
for candidate in "${CANDIDATES[@]}"; do
  if [[ -f "${candidate}" ]]; then
    BINARY="${candidate}"
    break
  fi
done

if [[ -z "${BINARY}" ]]; then
  echo "Cannot find built AmaryllisApp binary."
  exit 1
fi

rm -rf "${APP_BUNDLE}"
mkdir -p "${APP_BUNDLE}/Contents/MacOS"
mkdir -p "${APP_BUNDLE}/Contents/Resources"

cat > "${APP_BUNDLE}/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Amaryllis</string>
  <key>CFBundleDisplayName</key>
  <string>Amaryllis</string>
  <key>CFBundleIdentifier</key>
  <string>org.amaryllis.app</string>
  <key>CFBundleVersion</key>
  <string>0.1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleExecutable</key>
  <string>Amaryllis</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

cp "${BINARY}" "${APP_BUNDLE}/Contents/MacOS/Amaryllis"
chmod +x "${APP_BUNDLE}/Contents/MacOS/Amaryllis"
cp "${ICON_FILE}" "${APP_BUNDLE}/Contents/Resources/AppIcon.icns"

RESOURCE_DIR="$(dirname "${BINARY}")"
shopt -s nullglob
for bundle in "${RESOURCE_DIR}"/*.bundle; do
  cp -R "${bundle}" "${APP_BUNDLE}/Contents/Resources/"
done
shopt -u nullglob

echo "Built app bundle: ${APP_BUNDLE}"
