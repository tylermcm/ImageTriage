#!/usr/bin/env bash
# Builds the RAW preview worker (native/raw_preview) on Linux and macOS, as scripts/build_raw_preview.ps1 does on Windows.
#
#   bash scripts/build_raw_preview.sh --photocraft ../photocraft [--release] [--build-root DIR] [--target-root DIR]
#
# Prints the worker's path last. Freeze it into the AppImage with
#   IMAGE_TRIAGE_BUNDLE_RAW_PREVIEW_EXE=<that path> python setup_linux.py bdist_appimage
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHOTOCRAFT_ROOT=""
BUILD_ROOT="${IMAGE_TRIAGE_RAW_PREVIEW_BUILD_ROOT:-$REPO_ROOT/build/raw-preview}"
TARGET_ROOT=""
PROFILE="debug"

while (($#)); do
  case "$1" in
    --photocraft) PHOTOCRAFT_ROOT="$2"; shift 2 ;;
    --build-root) BUILD_ROOT="$2"; shift 2 ;;
    --target-root) TARGET_ROOT="$2"; shift 2 ;;
    --release) PROFILE="release"; shift ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

[[ -n "$PHOTOCRAFT_ROOT" ]] || { echo "--photocraft <PhotoCraft checkout> is required" >&2; exit 1; }
PHOTOCRAFT_ROOT="$(cd "$PHOTOCRAFT_ROOT" && pwd)"
[[ -f "$PHOTOCRAFT_ROOT/Cargo.toml" ]] || { echo "$PHOTOCRAFT_ROOT is not a PhotoCraft checkout" >&2; exit 1; }
TARGET_ROOT="${TARGET_ROOT:-$BUILD_ROOT/target}"
command -v cargo >/dev/null || { echo "cargo is required (https://rustup.rs)" >&2; exit 1; }

mkdir -p "$BUILD_ROOT"
# The manifest is a template (the worker builds against the PhotoCraft checkout's crates). Cargo only applies patches from the root
# manifest, and PhotoCraft's path dependencies need its pinned Hayro forks too, so its [patch.crates-io] table is copied in.
python3 - "$REPO_ROOT/native/raw_preview" "$PHOTOCRAFT_ROOT" "$BUILD_ROOT/Cargo.toml" <<'PY'
import re
import sys
from pathlib import Path

source, photocraft, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
manifest = (source / "Cargo.toml.in").read_text(encoding="utf-8")
manifest = manifest.replace("@PHOTOCRAFT@", photocraft.as_posix()).replace("@SOURCE@", source.as_posix())
patches = re.search(r"(?ms)^\[patch\.crates-io\][^\n]*\n.*?(?=^\[|\Z)", (photocraft / "Cargo.toml").read_text(encoding="utf-8"))
if patches:
    manifest += "\n" + patches.group(0)
out.write_text(manifest, encoding="utf-8")
PY
# Start from the same dependency pins as the editor, then let Cargo add this binary.
if [[ -f "$PHOTOCRAFT_ROOT/Cargo.lock" ]]; then
  cp "$PHOTOCRAFT_ROOT/Cargo.lock" "$BUILD_ROOT/Cargo.lock"
fi

args=(build --manifest-path "$BUILD_ROOT/Cargo.toml")
[[ "$PROFILE" == "release" ]] && args+=(--release)
CARGO_TARGET_DIR="$TARGET_ROOT" cargo "${args[@]}"

cp "$TARGET_ROOT/$PROFILE/image-triage-raw-preview" "$BUILD_ROOT/image-triage-raw-preview"
echo "Preview worker: $BUILD_ROOT/image-triage-raw-preview"
