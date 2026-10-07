"""Copy PocketDrop's engine and UI sources into native/pocketdrop/src.

PocketDrop (github.com/tylermcm/PocketDrop) is developed on its own; Image
Triage hosts its shared UI in a Qt panel. This re-vendors the parts the Qt host
builds against and records the commit they came from:

    python scripts/sync_pocketdrop.py [path-to-PocketDrop]

The PocketDrop checkout defaults to a sibling folder named PocketDrop. Its own
window hosts (main_*.cpp/.mm, gfx_*) are not copied: Image Triage replaces
them with native/pocketdrop/capi and image_triage/pocketdrop.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "native" / "pocketdrop" / "src"

# Relative to PocketDrop/src. Directories are copied whole.
SOURCES = (
    "core",
    "ui",
    "win/platform_win.cpp",
    "win/hotspot_win.cpp",
    "mac/platform_mac.mm",
    "linux/platform_linux.cpp",
)


def main() -> int:
    upstream = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "PocketDrop"
    src = upstream / "src"
    if not (src / "ui" / "ui.cpp").is_file():
        print(f"PocketDrop sources not found under {upstream}", file=sys.stderr)
        return 1
    if DEST.exists():
        shutil.rmtree(DEST)
    for rel in SOURCES:
        origin, target = src / rel, DEST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_dir():
            shutil.copytree(origin, target)
        else:
            shutil.copy2(origin, target)
    try:
        commit = subprocess.run(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(upstream), "status", "--porcelain", "--", "src"], capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unknown", ""
    (DEST.parent / "UPSTREAM").write_text(
        f"https://github.com/tylermcm/PocketDrop\n{commit}{' (with uncommitted changes)' if dirty else ''}\n",
        encoding="utf-8",
    )
    print(f"Vendored PocketDrop {commit[:10]} into {DEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
