"""Keep a photo's edit data with the photo when the app renames, moves, copies or deletes it.

Camera Raw recipes, PhotoCraft projects and their renders live in the folder's hidden edit root, named after the
photo (``IMG_1.NEF.cameraraw.json``, ``IMG_1.NEF.pcraft``, ``IMG_1.NEF.photocraft.*``). Nothing ties them to the photo
but that name, so a rename or move that leaves them behind makes the photo look unedited, and a later file that takes
the old name inherits edits that were never made to it.

The built-in editor's older ``<stem>.edit.json`` sessions are keyed by stem and shared between a RAW and its JPEG;
they are not handled here.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from .edit_storage import edit_root_for, ensure_edit_root

# What follows the photo's file name in an edit-data file name.
EDIT_MARKERS = (".cameraraw.json", ".pcraft", ".photocraft.")
_MANIFEST_TAIL = ".photocraft.renders.json"


def edit_files_for(image_path: str | os.PathLike) -> list[Path]:
    """The edit-data files that belong to this photo (existing ones only)."""
    photo = Path(image_path)
    root = edit_root_for(photo.parent)
    prefix = photo.name.casefold()
    found: list[Path] = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return found
    for entry in entries:
        name = entry.name
        if not name.casefold().startswith(prefix):
            continue
        tail = name[len(photo.name):]
        if tail.casefold().startswith(EDIT_MARKERS) and entry.is_file():
            found.append(Path(entry.path))
    return sorted(found)


def carry_edits(pairs) -> list[str]:
    """Move each photo's edit data from its old path to its new one. ``pairs`` is ``(old_image_path, new_image_path)``.

    Call it after the photos themselves have moved. Returns the names of edit files that could not follow (a file
    with that name is already there, or the move failed); those stay where they were. Never replaces a file.
    Chains and swaps (``a -> b`` while ``b -> a``) are safe: every file is parked under a temporary name first.
    """
    parked: list[tuple[Path, Path, Path, str, str]] = []     # (parked at, original, final, old photo name, new photo name)
    failed: list[str] = []
    for old_image, new_image in pairs:
        old, new = Path(old_image), Path(new_image)
        if old == new:
            continue
        files = edit_files_for(old)
        if not files:
            continue
        try:
            target_root = ensure_edit_root(new.parent)
        except OSError:
            failed.extend(item.name for item in files)
            continue
        for item in files:
            final = target_root / (new.name + item.name[len(old.name):])
            holding = target_root / f".carry-{uuid.uuid4().hex}.tmp"
            try:
                shutil.move(str(item), str(holding))
            except OSError:
                failed.append(item.name)
                continue
            parked.append((holding, item, final, old.name, new.name))
    for holding, original, final, old_name, new_name in parked:
        try:
            if final.exists():
                raise FileExistsError(final.name)
            os.replace(holding, final)
        except OSError:
            failed.append(original.name)
            try:                                              # put it back where it was rather than lose it
                if not original.exists():
                    shutil.move(str(holding), str(original))
            except OSError:
                pass
            continue
        if old_name != new_name and final.name.casefold().endswith(_MANIFEST_TAIL):
            _rename_manifest_outputs(final, old_name, new_name)
    return failed


def copy_edits(pairs) -> list[str]:
    """Give each copied photo a copy of the original's edit data. ``pairs`` is ``(original image path, copy's path)``.

    Call it after the photos themselves have been copied. The original keeps its edit data. Returns the names of edit
    files that could not be copied (a file with that name is already there, or the copy failed). Never replaces a file.
    """
    failed: list[str] = []
    for source_image, copied_image in pairs:
        source, copied = Path(source_image), Path(copied_image)
        if source == copied:
            continue
        files = edit_files_for(source)
        if not files:
            continue
        try:
            target_root = ensure_edit_root(copied.parent)
        except OSError:
            failed.extend(item.name for item in files)
            continue
        for item in files:
            final = target_root / (copied.name + item.name[len(source.name):])
            holding = target_root / f".carry-{uuid.uuid4().hex}.tmp"
            try:
                if final.exists():
                    raise FileExistsError(final.name)
                shutil.copy2(str(item), str(holding))
                if final.exists():
                    raise FileExistsError(final.name)
                os.replace(holding, final)
            except OSError:
                failed.append(item.name)
                try:
                    holding.unlink()
                except OSError:
                    pass
                continue
            if source.name != copied.name and final.name.casefold().endswith(_MANIFEST_TAIL):
                _rename_manifest_outputs(final, source.name, copied.name)
    return failed


def _rename_manifest_outputs(manifest: Path, old_name: str, new_name: str) -> None:
    """The render manifest names its pictures by file name; they were renamed with the photo, so the names in it follow."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        outputs = data.get("outputs") if isinstance(data, dict) else None
        if not isinstance(outputs, dict):
            return
        changed = False
        for described in outputs.values():
            name = described.get("file") if isinstance(described, dict) else None
            if isinstance(name, str) and name.casefold().startswith(old_name.casefold()):
                described["file"] = new_name + name[len(old_name):]
                changed = True
        if not changed:
            return
        holding = manifest.with_name(f".carry-{uuid.uuid4().hex}.tmp")
        holding.write_text(json.dumps(data), encoding="utf-8")
        os.replace(holding, manifest)
    except (OSError, ValueError):
        return                                               # an unreadable manifest only means the renders are made again
