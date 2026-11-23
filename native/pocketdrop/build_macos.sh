#!/usr/bin/env bash
# Builds image_triage/pocketdrop/libpocketdrop.dylib on a Mac: PocketDrop's engine and UI (src/, vendored by scripts/sync_pocketdrop.py)
# behind the C interface in capi/, as build_windows.bat and build_linux.sh do. The macOS platform layer is src/mac; the hotspot is the
# stub (src/core/hotspot_stub.cpp). libcurl and the frameworks are the system's: only the Xcode command line tools are needed
# (xcode-select --install).
#
#   bash native/pocketdrop/build_macos.sh [--arch arm64|x86_64]       (default: this Mac's)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/../../image_triage/pocketdrop/libpocketdrop.dylib"
ARCH="$(uname -m)"
while (($#)); do
  case "$1" in
    --arch) ARCH="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
[[ "$(uname -s)" == "Darwin" ]] || { echo "This builds the macOS library; run it on a Mac." >&2; exit 1; }
command -v clang++ >/dev/null || { echo "clang++ is required: xcode-select --install" >&2; exit 1; }

cd "$HERE"
SOURCES=(src/core/qr.cpp src/core/deflate.cpp src/core/bundle.cpp src/core/http.cpp src/core/tunnel.cpp src/core/nearby.cpp
         src/core/pc_transfer.cpp src/core/hotspot_stub.cpp src/ui/ui.cpp src/ui/icons.cpp src/mac/platform_mac.mm
         capi/pocketdrop_capi.cpp)
# The oldest macOS the app runs on (setup_macos.py's LSMinimumSystemVersion: PySide6 6.12 needs 14).
clang++ -std=c++20 -O2 -DNDEBUG -fPIC -shared -fvisibility=hidden -fobjc-arc -pthread \
  -arch "$ARCH" -mmacosx-version-min=14.0 -install_name @rpath/libpocketdrop.dylib \
  "${SOURCES[@]}" -o "$OUT" \
  -lcurl -framework Foundation -framework ImageIO -framework CoreGraphics -framework QuickLookThumbnailing \
  -framework Security -framework SystemConfiguration -framework AppKit -framework UniformTypeIdentifiers
echo "Built $OUT ($ARCH)"
