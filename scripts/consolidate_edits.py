"""Sweep scattered beside-photo edit bundles into the hidden per-folder root.

The editor used to write ``<stem>.edit.json`` + a ``<stem>.edit-assets`` folder
next to every original. Those now live in a single hidden ``.image_triage_edits``
root per folder. New edits already land there, and opening a photo migrates its
bundle on the fly, but this script clears an existing pile in one pass.

Usage::

    py -3.13 scripts/consolidate_edits.py "K:/Photography/Canada 10-25"
    py -3.13 scripts/consolidate_edits.py "K:/Photography" --dry-run

Only the editor's own reconstructable sidecar data is moved; originals are never
touched.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_triage.edit_storage import consolidate_folder, edit_root_for  # noqa: E402
from image_triage.scanner import (  # noqa: E402
    EDIT_STORAGE_ROOT_NAME,
    EDITOR_ASSET_DIR_SUFFIX,
    is_ignored_system_directory,
)


def _iter_shoot_folders(root: Path):
    """Yield ``root`` and every subfolder, skipping hidden/asset containers."""

    for current_dir, dirnames, _filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if not name.casefold().endswith(EDITOR_ASSET_DIR_SUFFIX)
            and not is_ignored_system_directory(name)
        ]
        yield Path(current_dir)


def _count_legacy_bundles(folder: Path) -> int:
    root = edit_root_for(folder)
    return sum(
        1
        for session in folder.glob("*.edit.json")
        if root not in session.parents
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Folder (or shoot tree) to sweep")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would move without touching anything",
    )
    args = parser.parse_args(argv)

    root = args.root.expanduser()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    total_folders = 0
    total_bundles = 0
    for folder in _iter_shoot_folders(root):
        if args.dry_run:
            pending = _count_legacy_bundles(folder)
            if pending:
                total_folders += 1
                total_bundles += pending
                print(f"{pending:4d}  {folder}  -> {folder / EDIT_STORAGE_ROOT_NAME}")
            continue
        moved = consolidate_folder(folder)
        if moved:
            total_folders += 1
            total_bundles += moved
            print(f"{moved:4d}  {folder}")

    verb = "would move" if args.dry_run else "moved"
    print(f"\n{verb} {total_bundles} bundle(s) across {total_folders} folder(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
