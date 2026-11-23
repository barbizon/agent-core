#!/usr/bin/env bash
# Builds Orwick.app into ./build. With --install, also copies it to ~/Applications.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="0.1.0"
APP="build/Orwick.app"

swift build -c release
BIN="$(swift build -c release --show-bin-path)/OrwickApp"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Orwick"
cp Resources/mermaid.min.js "$APP/Contents/Resources/"   # draws the Mermaid diagrams in docs and service maps

# The icon is the avatar, drawn by the app itself.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
"$BIN" --render-icon "$WORK/icon.png"
ICONSET="$WORK/Orwick.iconset"
mkdir "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$WORK/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$WORK/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Orwick.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Orwick</string>
  <key>CFBundleDisplayName</key><string>Orwick</string>
  <key>CFBundleIdentifier</key><string>local.orwick.app</string>
  <key>CFBundleExecutable</key><string>Orwick</string>
  <key>CFBundleIconFile</key><string>Orwick</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# Ad-hoc signature: enough to run on this Mac. Not for distribution.
codesign --force --sign - "$APP" >/dev/null

if [[ "${1:-}" == "--install" ]]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/Orwick.app"
  cp -R "$APP" "$HOME/Applications/"
  echo "Installed: $HOME/Applications/Orwick.app"
else
  echo "Built: $(pwd)/$APP"
fi
