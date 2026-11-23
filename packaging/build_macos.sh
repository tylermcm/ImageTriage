#!/usr/bin/env bash
# Builds Image Triage for macOS on a Mac: the PhotoCraft editor, the RAW preview worker and PocketDrop's library for one architecture,
# then the app bundle and its disk image, dist/ImageTriage-<version>-macos-<arch>.dmg.
#
#   bash packaging/build_macos.sh --photocraft ../photocraft [--arch arm64|x86_64] [--python /path/to/python3.13]
#
# Both architectures can be built on an Apple Silicon Mac: --arch x86_64 runs the Python side under Rosetta and cross-compiles the Rust
# side. That needs the python.org Python 3.13 installer (a universal2 build that runs as either) and, once,
#   rustup target add x86_64-apple-darwin
# An Intel Mac builds x86_64 only. The PhotoCraft checkout must be at the commit in packaging/photocraft_pin.txt.
#
# Signing: without IMAGE_TRIAGE_CODESIGN_IDENTITY the bundle is signed ad hoc, which Apple Silicon needs to run it at all; Gatekeeper
# then asks the person to confirm the first start (Control-click > Open). With a "Developer ID Application: …" identity it is signed for
# distribution; notarize the .dmg afterwards (docs/macos.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(uname -m)"
PHOTOCRAFT=""
PYTHON="${IMAGE_TRIAGE_MACOS_PYTHON:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13}"
while (($#)); do
  case "$1" in
    --arch) ARCH="$2"; shift 2 ;;
    --photocraft) PHOTOCRAFT="$2"; shift 2 ;;
    --python) PYTHON="$2"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)" == "Darwin" ]] || fail "This builds the macOS app; run it on a Mac."
[[ "$ARCH" == "arm64" || "$ARCH" == "x86_64" ]] || fail "--arch is arm64 or x86_64, not $ARCH"
[[ -n "$PHOTOCRAFT" ]] || fail "--photocraft <PhotoCraft checkout at packaging/photocraft_pin.txt> is required"
PHOTOCRAFT="$(cd "$PHOTOCRAFT" && pwd)"
[[ -x "$PYTHON" ]] || fail "No Python 3.13 at $PYTHON. Install it from python.org (the universal2 installer) or pass --python."
command -v cargo >/dev/null || fail "Rust is required (https://rustup.rs)."
command -v clang++ >/dev/null || fail "The Xcode command line tools are required: xcode-select --install"

PIN="$(tr -d '[:space:]' < "$ROOT/packaging/photocraft_pin.txt")"
HEAD="$(git -C "$PHOTOCRAFT" rev-parse HEAD)"
[[ "$HEAD" == "$PIN" ]] || echo "Warning: $PHOTOCRAFT is at $HEAD, not the pinned $PIN." >&2

TRIPLE="$([[ "$ARCH" == "arm64" ]] && echo aarch64-apple-darwin || echo x86_64-apple-darwin)"
# The Python side runs as the architecture being built: under Rosetta for x86_64 on an Apple Silicon Mac.
# (A function, not an array: macOS's own bash 3.2 refuses an empty "${array[@]}" under set -u.)
run() {
  if [[ "$ARCH" != "$(uname -m)" ]]; then arch "-$ARCH" "$@"; else "$@"; fi
}
if [[ "$ARCH" == "x86_64" && "$(uname -m)" == "arm64" ]]; then
  # Under Rosetta, the command line tools' own programs (otool, install_name_tool: cx_Freeze runs them on every binary it freezes)
  # cannot start: their xcrun shim is arm64 only. They read and change x86_64 files just as well run natively, so the build gets
  # wrappers that run them as arm64.
  TOOLS="$ROOT/build/macos-x86_64/native-tools"
  mkdir -p "$TOOLS"
  for tool in otool install_name_tool lipo strip nm libtool codesign_allocate; do
    printf '#!/bin/bash\nexec /usr/bin/arch -arm64 /usr/bin/%s "$@"\n' "$tool" > "$TOOLS/$tool"
    chmod +x "$TOOLS/$tool"
  done
  export PATH="$TOOLS:$PATH"
fi
export MACOSX_DEPLOYMENT_TARGET=14.0          # PySide6 6.12 needs macOS 14 (setup_macos.py's LSMinimumSystemVersion)
BUILD="$ROOT/build/macos-$ARCH"
mkdir -p "$BUILD"

echo "== PhotoCraft ($TRIPLE)"
(cd "$PHOTOCRAFT" && CARGO_TARGET_DIR="$BUILD/photocraft-target" cargo build --release --locked -p photocraft --target "$TRIPLE")
EDITOR="$BUILD/photocraft-target/$TRIPLE/release/photocraft"

echo "== RAW preview worker ($TRIPLE)"
bash "$ROOT/scripts/build_raw_preview.sh" --photocraft "$PHOTOCRAFT" --release --target "$TRIPLE" \
  --build-root "$BUILD/raw-preview" --target-root "$BUILD/raw-preview/target"
WORKER="$BUILD/raw-preview/image-triage-raw-preview"

echo "== PocketDrop ($ARCH)"
bash "$ROOT/native/pocketdrop/build_macos.sh" --arch "$ARCH"

echo "== Python environment ($ARCH)"
VENV="$ROOT/.macos_build_venv-$ARCH"
[[ -x "$VENV/bin/python" ]] || run "$PYTHON" -m venv "$VENV"
run "$VENV/bin/python" -m pip install --upgrade pip setuptools wheel
# --prefer-binary: never compile a dependency. Under Rosetta nothing can be compiled (the command line tools' compiler driver is arm64
# only), and some packages' newest release has no Intel wheel (rawpy 0.28; 0.25.1 has one), so take the newest one that has a wheel.
run "$VENV/bin/python" -m pip install --prefer-binary -e "$ROOT" cx_Freeze
run "$VENV/bin/python" -c "import platform, sys; assert platform.machine() == '$ARCH', platform.machine(); print(sys.version)"

echo "== App bundle and disk image"
cd "$ROOT"
VERSION="$(run "$VENV/bin/python" -c 'from freeze_support import read_project_version; print(read_project_version())')"
rm -rf "$ROOT/build/exe.macosx-"* "$ROOT/build/Image Triage.app" "$ROOT/build/Image Triage $VERSION.dmg"
SIGN=(--codesign-identity=- --codesign-deep=1)                 # ad hoc: Apple Silicon runs nothing unsigned
if [[ -n "${IMAGE_TRIAGE_CODESIGN_IDENTITY:-}" ]]; then
  # A Developer ID, for distribution: the hardened runtime (notarization requires it) with the exceptions the app needs.
  SIGN=(--codesign-identity="$IMAGE_TRIAGE_CODESIGN_IDENTITY" --codesign-deep=1 --codesign-options=runtime --codesign-timestamp
        --codesign-entitlements="$ROOT/packaging/macos.entitlements")
fi
IMAGE_TRIAGE_BUNDLE_PHOTOCRAFT_EXE="$EDITOR" IMAGE_TRIAGE_BUNDLE_RAW_PREVIEW_EXE="$WORKER" \
  run "$VENV/bin/python" setup_macos.py bdist_mac "${SIGN[@]}"

# cx_Freeze makes the app's own programs from python.org's universal Python, so they hold both architectures while every library
# frozen beside them holds one. A universal program starts as arm64 on Apple Silicon and then cannot load an x86_64 NumPy: the Intel
# build would not run there under Rosetta, and the Apple Silicon build would crash on an Intel Mac instead of being refused. Keep only
# this build's architecture in each of them, then sign the bundle again (thinning breaks the signature).
APP="$ROOT/build/Image Triage.app"
for program in "$APP"/Contents/MacOS/*; do
  [[ -f "$program" && ! -L "$program" ]] || continue
  archs="$(lipo -archs "$program" 2>/dev/null || true)"
  if [[ "$archs" == *" "* ]]; then
    lipo -thin "$ARCH" "$program" -output "$program.thin" && mv "$program.thin" "$program" && chmod +x "$program"
    echo "thinned $(basename "$program"): $archs -> $ARCH"
  fi
done
# Which signature: a Developer ID (IMAGE_TRIAGE_CODESIGN_IDENTITY) for a release; else the development certificate of
# packaging/macos_dev_signing.sh when this Mac has it, so test builds keep one signature and macOS keeps its answers between them;
# else ad hoc.
DEV_KEYCHAIN="$HOME/Library/Keychains/imagetriage-dev.keychain-db"
if [[ -n "${IMAGE_TRIAGE_CODESIGN_IDENTITY:-}" ]]; then
  codesign --force --deep --options runtime --timestamp --entitlements "$ROOT/packaging/macos.entitlements" \
    --sign "$IMAGE_TRIAGE_CODESIGN_IDENTITY" "$APP"
elif [[ -f "$DEV_KEYCHAIN" && -f "$HOME/.imagetriage-signing/keychain-password" ]]; then
  security unlock-keychain -p "$(cat "$HOME/.imagetriage-signing/keychain-password")" "$DEV_KEYCHAIN"
  codesign --force --deep --keychain "$DEV_KEYCHAIN" --sign "Image Triage Development" "$APP"
  echo "signed with the development certificate"
else
  codesign --force --deep --sign - "$APP"
fi
codesign --verify --deep --strict "$APP"

# The disk image: the app and a link to /Applications to drag it onto. Made here with hdiutil rather than cx_Freeze's bdist_dmg, whose
# dmgbuild runs /usr/bin/SetFile, a command line tools shim that cannot start under Rosetta (the x86_64 build on an Apple Silicon Mac).
mkdir -p "$ROOT/dist"
DMG="$ROOT/dist/ImageTriage-$VERSION-macos-$ARCH.dmg"
STAGE="$BUILD/dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$ROOT/build/Image Triage.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Image Triage $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG"
rm -rf "$STAGE"
(cd "$ROOT/dist" && shasum -a 256 "ImageTriage-$VERSION-macos-$ARCH.dmg" | tee "ImageTriage-$VERSION-macos-$ARCH.dmg.sha256")   # the name alone, so it checks after a download
echo "Built dist/ImageTriage-$VERSION-macos-$ARCH.dmg"
