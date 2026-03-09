#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ASSETS_DIR="${PROJECT_DIR}/Assets"
ICONSET_DIR="${ASSETS_DIR}/AppIcon.iconset"
BASE_PNG="${ASSETS_DIR}/AppIcon-1024.png"
ICON_FILE="${ASSETS_DIR}/AppIcon.icns"

mkdir -p "${ASSETS_DIR}"

swift - "${BASE_PNG}" <<'SWIFT'
import AppKit

let outputPath = CommandLine.arguments[1]
let outputURL = URL(fileURLWithPath: outputPath)
let size: CGFloat = 1024
let image = NSImage(size: NSSize(width: size, height: size))

image.lockFocus()

let background = NSRect(x: 0, y: 0, width: size, height: size)
NSColor(calibratedRed: 0.07, green: 0.07, blue: 0.08, alpha: 1.0).setFill()
NSBezierPath(rect: background).fill()

let outer = NSBezierPath(roundedRect: NSRect(x: 60, y: 60, width: 904, height: 904), xRadius: 220, yRadius: 220)
NSColor(calibratedRed: 0.12, green: 0.12, blue: 0.13, alpha: 1.0).setFill()
outer.fill()

let center = NSPoint(x: size / 2, y: size / 2)
let petals: [(CGFloat, CGFloat)] = [
    (0, 220),
    (190, 80),
    (140, -150),
    (-140, -150),
    (-190, 80)
]

for (idx, offset) in petals.enumerated() {
    let r = CGFloat(150 - idx * 10)
    let rect = NSRect(
        x: center.x + offset.0 - r,
        y: center.y + offset.1 - r,
        width: r * 2,
        height: r * 2
    )
    let path = NSBezierPath(ovalIn: rect)
    NSColor(
        calibratedRed: 0.68,
        green: 0.10 + CGFloat(idx) * 0.01,
        blue: 0.20,
        alpha: 0.95
    ).setFill()
    path.fill()
}

let core = NSBezierPath(ovalIn: NSRect(x: center.x - 92, y: center.y - 92, width: 184, height: 184))
NSColor(calibratedRed: 0.88, green: 0.22, blue: 0.30, alpha: 1.0).setFill()
core.fill()

image.unlockFocus()

guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("Failed to create icon image")
}

try png.write(to: outputURL)
SWIFT

rm -rf "${ICONSET_DIR}"
mkdir -p "${ICONSET_DIR}"

for size in 16 32 128 256 512; do
  sips -z "${size}" "${size}" "${BASE_PNG}" --out "${ICONSET_DIR}/icon_${size}x${size}.png" >/dev/null
  sips -z "$((size * 2))" "$((size * 2))" "${BASE_PNG}" --out "${ICONSET_DIR}/icon_${size}x${size}@2x.png" >/dev/null
done

iconutil -c icns "${ICONSET_DIR}" -o "${ICON_FILE}"
echo "Generated icon: ${ICON_FILE}"
