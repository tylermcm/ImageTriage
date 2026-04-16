#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${IMAGE_TRIAGE_LINUX_VENV:-$ROOT_DIR/.linux_build_venv}"
INSTALL_DIR="${IMAGE_TRIAGE_LINUX_INSTALL_DIR:-$HOME/.local/opt/ImageTriage}"
LAUNCHER_PATH="${IMAGE_TRIAGE_LINUX_LAUNCHER:-$HOME/.local/bin/image-triage}"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/512x512/apps"
ICON_SOURCE_PATH="$ROOT_DIR/build_assets/icons/image_triage.png"
APPIMAGE_PATH=""
SKIP_BUILD=0
NO_DESKTOP=0

usage() {
  cat <<'EOF'
Usage: bash packaging/install_linux.sh [options]

Options:
  --appimage PATH     Install an existing AppImage instead of building one.
  --skip-build        Skip the AppImage build step and install the newest file from ./dist.
  --venv PATH         Override the build virtualenv path.
  --install-dir PATH  Override the install directory. Default: ~/.local/opt/ImageTriage
  --no-desktop        Skip desktop entry creation.
  -h, --help          Show this help text.

Environment overrides:
  IMAGE_TRIAGE_AI_SOURCE           Override the AICullingPipeline source path.
  IMAGE_TRIAGE_AI_SITE_PACKAGES    Override the AI site-packages source path.
  IMAGE_TRIAGE_AI_STDLIB           Override the AI stdlib source path.
  IMAGE_TRIAGE_AI_DLLS             Override the AI binary modules source path.
EOF
}

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --appimage)
      [[ $# -ge 2 ]] || fail "--appimage requires a path"
      APPIMAGE_PATH="$2"
      shift 2
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --venv)
      [[ $# -ge 2 ]] || fail "--venv requires a path"
      VENV_DIR="$2"
      shift 2
      ;;
    --install-dir)
      [[ $# -ge 2 ]] || fail "--install-dir requires a path"
      INSTALL_DIR="$2"
      shift 2
      ;;
    --no-desktop)
      NO_DESKTOP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

[[ "$(uname -s)" == "Linux" ]] || fail "This installer only runs on Linux."
command -v python3 >/dev/null 2>&1 || fail "python3 is required."

ensure_build_venv() {
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    python3 -m venv "$VENV_DIR"
  fi

  "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
  "$VENV_DIR/bin/python" -m pip install -e "$ROOT_DIR"
  "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/packaging/linux-build-requirements.txt"
}

build_appimage() {
  export IMAGE_TRIAGE_AI_SOURCE="${IMAGE_TRIAGE_AI_SOURCE:-$ROOT_DIR/AICullingPipeline}"
  "$VENV_DIR/bin/python" "$ROOT_DIR/setup_linux.py" bdist_appimage
}

discover_latest_appimage() {
  local newest
  newest="$(find "$ROOT_DIR/dist" -maxdepth 1 -type f -name '*.AppImage' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)"
  [[ -n "$newest" ]] || fail "No AppImage found in $ROOT_DIR/dist"
  APPIMAGE_PATH="$newest"
}

install_appimage() {
  local target_appimage icon_target desktop_path
  target_appimage="$INSTALL_DIR/ImageTriage.AppImage"
  icon_target="$ICON_INSTALL_DIR/image-triage.png"
  desktop_path="$APPLICATIONS_DIR/image-triage.desktop"

  mkdir -p "$INSTALL_DIR" "$(dirname "$LAUNCHER_PATH")"
  cp "$APPIMAGE_PATH" "$target_appimage"
  chmod +x "$target_appimage"

  cat > "$LAUNCHER_PATH" <<EOF
#!/usr/bin/env bash
set -euo pipefail
APPIMAGE="$target_appimage"
if command -v ldconfig >/dev/null 2>&1 && ldconfig -p 2>/dev/null | grep -q 'libfuse\.so\.2'; then
  exec "\$APPIMAGE" "\$@"
fi
exec "\$APPIMAGE" --appimage-extract-and-run "\$@"
EOF
  chmod +x "$LAUNCHER_PATH"

  if (( NO_DESKTOP == 0 )); then
    mkdir -p "$APPLICATIONS_DIR" "$ICON_INSTALL_DIR"
    cp "$ICON_SOURCE_PATH" "$icon_target"
    cat > "$desktop_path" <<EOF
[Desktop Entry]
Type=Application
Name=Image Triage
Exec=$LAUNCHER_PATH %F
Icon=$icon_target
Comment=Browse, cull, and score large image folders
Categories=Graphics;Photography;
Terminal=false
StartupNotify=true
EOF
  fi

  printf '\nInstalled Image Triage:\n'
  printf '  Launcher: %s\n' "$LAUNCHER_PATH"
  printf '  AppImage: %s\n' "$target_appimage"
  if (( NO_DESKTOP == 0 )); then
    printf '  Desktop entry: %s\n' "$desktop_path"
  fi
  printf '\nOn first launch, the app will offer to download the AI model into:\n'
  printf '  %s\n' "${XDG_CACHE_HOME:-$HOME/.cache}/image_triage_ai_cache/models/Skulleton12/DinoV2"
  printf 'You can skip that step and later use AI > Download AI Model... inside the app.\n'
}

if [[ -z "$APPIMAGE_PATH" ]]; then
  if (( SKIP_BUILD == 0 )); then
    ensure_build_venv
    build_appimage
  fi
  discover_latest_appimage
fi

[[ -f "$APPIMAGE_PATH" ]] || fail "AppImage not found: $APPIMAGE_PATH"
install_appimage
