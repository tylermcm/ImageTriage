#!/usr/bin/env bash
# Builds image_triage/pocketdrop/libpocketdrop.so: PocketDrop's engine and UI (src/, vendored by scripts/sync_pocketdrop.py) behind the
# C interface in capi/, as build_windows.bat builds pocketdrop.dll. The Linux counterpart of the Windows platform layer is src/linux.
#
# Needs g++ (or clang++) with C++20, pkg-config, and the libcurl and gdk-pixbuf development files:
#   sudo apt install g++ pkg-config libcurl4-openssl-dev libgdk-pixbuf-2.0-dev      (Debian, Ubuntu)
#   sudo dnf install gcc-c++ pkgconf libcurl-devel gdk-pixbuf2-devel                 (Fedora)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/../../image_triage/pocketdrop/libpocketdrop.so"
CXX="${CXX:-$(command -v g++ || command -v clang++ || true)}"
[[ -n "$CXX" ]] || { echo "A C++20 compiler (g++ or clang++) is required." >&2; exit 1; }
command -v pkg-config >/dev/null || { echo "pkg-config is required." >&2; exit 1; }
pkg-config --exists libcurl gdk-pixbuf-2.0 || {
  echo "The libcurl and gdk-pixbuf development files are required (libcurl4-openssl-dev and libgdk-pixbuf-2.0-dev on Debian and Ubuntu)." >&2
  exit 1
}

cd "$HERE"
SOURCES=(src/core/qr.cpp src/core/deflate.cpp src/core/bundle.cpp src/core/http.cpp src/core/tunnel.cpp src/core/nearby.cpp
         src/core/pc_transfer.cpp src/core/hotspot_stub.cpp src/ui/ui.cpp src/ui/icons.cpp src/linux/platform_linux.cpp
         src/linux/hotspot_linux.cpp capi/pocketdrop_capi.cpp)
# shellcheck disable=SC2046  # pkg-config's flags are meant to be split into words
"$CXX" -std=c++20 -O2 -DNDEBUG -fPIC -shared -fvisibility=hidden -pthread -Wl,--as-needed -Wl,-z,defs \
  "${SOURCES[@]}" -o "$OUT" $(pkg-config --cflags --libs libcurl gdk-pixbuf-2.0)
echo "Built $OUT"
