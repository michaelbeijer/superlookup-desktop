#!/bin/bash
# =============================================================================
# SuperLookup — macOS signed & notarized build
# =============================================================================
# Builds SuperLookup.app with PyInstaller (onedir, windowed), code-signs it
# inside-out for distribution with a Developer ID Application certificate and
# the hardened runtime, packages a DMG, notarizes with Apple, and staples the
# ticket to both the .app and the DMG.
#
# This is a QtWebEngine (Chromium) app, so signing is done INSIDE-OUT — every
# nested .dylib/.so/.framework and the nested QtWebEngineProcess.app helper are
# signed before the outer bundle. `--deep` is deliberately NOT used (it does not
# apply the correct per-helper entitlements and Apple discourages it).
#
# Usage:
#   ./build_macos.sh                     # full pipeline (build → sign → dmg → notarize → staple)
#   ./build_macos.sh --skip-notarize     # build + sign + dmg only
#   ./build_macos.sh --no-smoke          # skip the GUI launch smoke test
#
# Override via environment:
#   PY312=/path/to/python3.12            # default: Homebrew python@3.12
#   CODESIGN_IDENTITY="Developer ID Application: NAME (TEAMID)"   # default: auto-detect
#   NOTARY_PROFILE="superlookup-notary"  # notarytool keychain profile
#
# Prerequisites:
#   - Apple Developer Program membership + "Developer ID Application" cert in Keychain
#   - Notary credentials stored once:
#       xcrun notarytool store-credentials "superlookup-notary" \
#           --apple-id "you@example.com" --team-id "TEAMID" --password "app-specific-pw"
#   - Python 3.12  (brew install python@3.12)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Configuration ────────────────────────────────────────────────────────────
APP_NAME="SuperLookup"
ENTRY="superlookup.py"
VENV_DIR=".venv"
DIST="dist"
APP_PATH="$DIST/$APP_NAME.app"
# NOTE: name is load-bearing — the macOS auto-updater downloads this EXACT
# filename, mounts it, and swaps in the .app at the DMG root. Do not rename.
DMG_PATH="$DIST/SuperLookup-macos.dmg"
ENTITLEMENTS="packaging/entitlements.plist"

PY312="${PY312:-/opt/homebrew/opt/python@3.12/bin/python3.12}"
NOTARY_PROFILE="${NOTARY_PROFILE:-superlookup-notary}"

# ── Flags ────────────────────────────────────────────────────────────────────
SKIP_NOTARIZE=false
SMOKE=true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-notarize) SKIP_NOTARIZE=true; shift ;;
        --no-smoke)      SMOKE=false; shift ;;
        -h|--help)       sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo ""
echo "=== SuperLookup macOS signed build ==="
echo ""

# ── Auto-detect signing identity if not provided ─────────────────────────────
if [ -z "${CODESIGN_IDENTITY:-}" ]; then
    CODESIGN_IDENTITY=$(security find-identity -v -p codesigning \
        | grep "Developer ID Application" | head -1 \
        | sed -E 's/.*"(.*)".*/\1/')
    if [ -z "$CODESIGN_IDENTITY" ]; then
        echo "ERROR: No 'Developer ID Application' identity found in keychain."
        echo "       security find-identity -v -p codesigning"
        exit 1
    fi
fi
echo "Signing identity:  $CODESIGN_IDENTITY"
echo "Notary profile:    $NOTARY_PROFILE"
echo ""

if [ ! -f "$ENTITLEMENTS" ]; then
    echo "ERROR: entitlements not found at $ENTITLEMENTS"; exit 1
fi

# ── 1. Python 3.12 venv + dependencies ───────────────────────────────────────
if [ ! -x "$PY312" ] && ! command -v "$PY312" >/dev/null 2>&1; then
    echo "ERROR: python3.12 not found at '$PY312'. Install with: brew install python@3.12"
    echo "       or set PY312=/path/to/python3.12"
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python 3.12 venv..."
    "$PY312" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
echo "Python: $(python --version)"
echo "Installing dependencies (requirements.txt + pyinstaller)..."
pip install --upgrade pip -q
pip install -r requirements.txt pyinstaller -q

# ── 2. Build the .app bundle (onedir, windowed) ──────────────────────────────
# NOTE: CI (.github/workflows/build.yml) uses --onefile. We deliberately build
# ONEDIR here: inside-out signing of the nested QtWebEngineProcess.app helper
# and per-framework signing require a real .app/Contents/Frameworks tree, which
# --onefile does not produce. All other CI flags are reused verbatim.
echo ""
echo "=== Building $APP_NAME.app (PyInstaller, onedir/windowed) ==="
pkill -x "$APP_NAME" 2>/dev/null || true
rm -rf "$DIST/$APP_NAME" "$APP_PATH" "build/$APP_NAME"
pyinstaller --noconfirm --clean --windowed --name "$APP_NAME" \
    --exclude-module PyQt5 --exclude-module PySide6 --exclude-module PySide2 \
    --hidden-import ApplicationServices \
    "$ENTRY"

if [ ! -d "$APP_PATH" ]; then
    echo "ERROR: build failed — $APP_PATH not found."; exit 1
fi
echo "  ✓ Built $APP_PATH"

# Stamp the real version into Info.plist BEFORE signing. PyInstaller defaults
# CFBundleShortVersionString to 0.0.0, which is what shows up in crash reports.
# Read VERSION from superlookup.py (single source of truth).
APP_VERSION="$(sed -n 's/^VERSION *= *"\(.*\)".*/\1/p' "$ENTRY" | head -1)"
if [ -n "$APP_VERSION" ]; then
    PLIST="$APP_PATH/Contents/Info.plist"
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $APP_VERSION" "$PLIST" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $APP_VERSION" "$PLIST"
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $APP_VERSION" "$PLIST" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $APP_VERSION" "$PLIST"
    echo "  ✓ Info.plist version set to $APP_VERSION"
fi

# Snapshot the pristine bundle. superlookup.py writes its runtime data (webdata/,
# easylist_domains.txt, config.json, ...) next to __file__, i.e. INTO the bundle
# (superlookup.py:94). The smoke-test launch below therefore pollutes the bundle
# with unsigned, non-code files that break the signature seal. We diff against
# this baseline afterwards and delete anything the launch created.
BASELINE="$(mktemp)"
( cd "$APP_PATH" && find . | sort ) > "$BASELINE"

# ── 3. Smoke test: confirm it launches ───────────────────────────────────────
if [ "$SMOKE" = true ]; then
    echo ""
    echo "=== Launch smoke test ==="
    open "$APP_PATH"
    sleep 6
    if pgrep -x "$APP_NAME" >/dev/null 2>&1; then
        echo "  ✓ $APP_NAME launched and is running"
        pkill -x "$APP_NAME" 2>/dev/null || true
        sleep 1
    else
        echo "  ✗ $APP_NAME did not stay running — check the build."
        exit 1
    fi
fi

# ── 4. Code sign inside-out (hardened runtime, NO --deep) ─────────────────────
echo ""
echo "=== Code signing (inside-out, hardened runtime) ==="

# Remove everything the smoke-test launch wrote into the bundle (see BASELINE
# snapshot above): runtime data is not code, must not ship, and breaks the seal.
echo "Cleaning runtime-generated data from bundle before signing..."
AFTER="$(mktemp)"
( cd "$APP_PATH" && find . | sort ) > "$AFTER"
comm -13 "$BASELINE" "$AFTER" | while IFS= read -r p; do
    [ -n "$p" ] && rm -rf "$APP_PATH/${p#./}"
done
rm -f "$BASELINE" "$AFTER"
find "$APP_PATH" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

sign() {  # sign <target>
    codesign --force --sign "$CODESIGN_IDENTITY" --timestamp \
        --options runtime --entitlements "$ENTITLEMENTS" "$1"
}

# 4a. All Mach-O libraries (.so, .dylib), deepest paths first.
echo "Signing .so / .dylib libraries..."
find "$APP_PATH" \( -name "*.so" -o -name "*.dylib" \) -type f -print0 \
    | while IFS= read -r -d '' f; do sign "$f"; done

# 4b. Nested QtWebEngineProcess.app helper — executable first, then its bundle.
HELPER="$APP_PATH/Contents/Frameworks/PyQt6/Qt6/lib/QtWebEngineCore.framework/Versions/A/Helpers/QtWebEngineProcess.app"
if [ -d "$HELPER" ]; then
    echo "Signing QtWebEngineProcess helper (same entitlements)..."
    sign "$HELPER/Contents/MacOS/QtWebEngineProcess"
    sign "$HELPER"
    echo "  ✓ QtWebEngineProcess signed"
else
    echo "  WARNING: QtWebEngineProcess helper not found at expected path:"
    echo "           $HELPER"
fi

# 4c. All frameworks, deepest-first.
echo "Signing frameworks..."
find "$APP_PATH/Contents/Frameworks" -name "*.framework" -type d -depth -print0 \
    | while IFS= read -r -d '' f; do sign "$f"; done

# 4d. Main executable, then 4e. the outer .app bundle.
echo "Signing main executable + outer app..."
sign "$APP_PATH/Contents/MacOS/$APP_NAME"
sign "$APP_PATH"
echo "  ✓ Signing complete"

# ── 5. Verify signature ──────────────────────────────────────────────────────
echo ""
echo "=== Verifying signature ==="
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
echo "  ✓ codesign --verify passed"
spctl -a -vvv "$APP_PATH" || echo "  (spctl reject before notarization is expected)"

# ── 6. Package DMG ───────────────────────────────────────────────────────────
echo ""
echo "=== Creating DMG ==="
rm -f "$DMG_PATH"
STAGING="$(mktemp -d)"
cp -R "$APP_PATH" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING" \
    -ov -format UDZO "$DMG_PATH" >/dev/null
rm -rf "$STAGING"
codesign --force --sign "$CODESIGN_IDENTITY" --timestamp "$DMG_PATH"
echo "  ✓ DMG created and signed: $DMG_PATH"

# ── 7. Notarize + staple both .app and DMG ───────────────────────────────────
if [ "$SKIP_NOTARIZE" = true ]; then
    echo ""
    echo "=== Notarization SKIPPED (--skip-notarize) ==="
else
    echo ""
    echo "=== Notarizing with Apple (profile: $NOTARY_PROFILE) ==="
    xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait

    echo ""
    echo "Stapling ticket to .app and DMG..."
    xcrun stapler staple "$APP_PATH"
    xcrun stapler staple "$DMG_PATH"

    echo ""
    echo "Validating staples..."
    xcrun stapler validate "$APP_PATH"
    xcrun stapler validate "$DMG_PATH"
    echo "  ✓ Notarized and stapled"
fi

echo ""
echo "========================================"
echo "  Build complete"
echo "  App: $APP_PATH"
echo "  DMG: $DMG_PATH"
echo "========================================"
