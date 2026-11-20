#!/bin/sh
# Builds "PDF Redact.app" and its disk image on an Apple Silicon Mac. From the repository folder:
#
#     uv sync --no-offline --group mac-build      (once: downloads the app's dependencies and PyInstaller)
#     sh packaging/macos/build.sh
#
# Makes dist/PDF Redact.app and dist/PDF Redact-<version>-arm64.dmg. Nothing here uses the network.
set -eu
cd "$(dirname "$0")/../.."

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "Build the Mac app on an Apple Silicon Mac." >&2
    exit 1
fi
NAME="PDF Redact"
APP="dist/$NAME.app"
VERSION=$(uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
DMG="dist/$NAME-$VERSION-arm64.dmg"

rm -rf build dist

echo "== icon"
uv run python packaging/macos/make_icon.py "build/$NAME.iconset"
iconutil -c icns "build/$NAME.iconset" -o "build/$NAME.icns"

echo "== app"
uv run --group mac-build pyinstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/macos/pdf-redact.spec

echo "== signature (local, for this Mac)"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "== self-check"
"$APP/Contents/MacOS/$NAME" --self-check

echo "== disk image"
mkdir -p build/dmg
ditto "$APP" "build/dmg/$NAME.app"
ln -s /Applications build/dmg/Applications
hdiutil create -volname "$NAME" -srcfolder build/dmg -ov -format UDZO "$DMG"

echo "Done: $APP"
echo "      $DMG"
