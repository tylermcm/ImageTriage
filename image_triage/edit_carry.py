"""Keep a photo's edit data with the photo when the app renames, moves, copies or deletes it.

Camera Raw recipes, PhotoCraft projects and their renders live in the folder's hidden edit root, named after the
photo (``IMG_1.NEF.cameraraw.json``, ``IMG_1.NEF.pcraft``, ``IMG_1.NEF.photocraft.*``). Nothing ties them to the photo
but that name, so a rename or move that leaves them behind makes the photo look unedited, and a later file that takes
the old name inherits edits that were never made to it.

The built-in editor's sessions are different: ``<stem>.edit.json`` with a ``<stem>.edit-assets`` folder, in the hidden
root or (older) beside the photo, keyed by the stem and so shared by every photo with that stem (a RAW and its JPEG).
A session follows its photos when none with that stem stays behind; when one does stay, the session is copied, so
neither side loses its edits. The session records its assets folder by name, which is rewritten when the stem changes.

Nothing here ever replaces edit data that is already at the destination.
"""
from __future__ import annotations

import filecmp
import json
import os
import shutil
import uuid
from pathlib import Path

from .edit_storage import edit_root_for, ensure_edit_root
from .scanner import EDIT_STORAGE_ROOT_NAME, IMAGE_SUFFIXES

# What follows the photo's file name in an edit-data file name.
EDIT_MARKERS = (".cameraraw.json", ".pcraft", ".photocraft.")
_MANIFEST_TAIL = ".photocraft.renders.json"
SESSION_SUFFIX = ".edit.json"
SESSION_ASSETS_SUFFIX = ".edit-assets"


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


def session_files_for(image_path: str | os.PathLike) -> list[Path]:
    """The built-in editor's session for this photo's stem: the session file and its assets folder, in the hidden edit
    root and (the older layout) beside the photo. Existing ones only."""
    photo = Path(image_path)
    if photo.suffix.casefold() not in IMAGE_SUFFIXES:
        return []
    found: list[Path] = []
    for folder in (edit_root_for(photo.parent), photo.parent):
        for name in (photo.stem + SESSION_SUFFIX, photo.stem + SESSION_ASSETS_SUFFIX):
            candidate = folder / name
            if os.path.lexists(candidate):
                found.append(candidate)
    return found


def stem_stays(image_path: str | os.PathLike, leaving=()) -> bool:
    """Is another photo with this photo's stem in its folder, besides the ones in ``leaving``? Its session is then not
    this photo's alone to take."""
    photo = Path(image_path)
    gone = {os.path.normcase(os.path.normpath(str(path))) for path in (photo, *leaving)}
    stem = photo.stem.casefold()
    try:
        with os.scandir(photo.parent) as entries:
            for entry in entries:
                other = Path(entry.name)
                if other.stem.casefold() == stem and other.suffix.casefold() in IMAGE_SUFFIXES \
                        and os.path.normcase(os.path.normpath(entry.path)) not in gone and entry.is_file():
                    return True
    except OSError:
        return False
    return False


def deletable_edit_paths(image_paths) -> list[Path]:
    """Everything that goes when the photo made of ``image_paths`` is deleted: its edit files, and its session unless
    another photo with the same stem stays."""
    images = [Path(path) for path in image_paths]
    found: list[Path] = []
    seen: set[str] = set()
    for image in images:
        items = list(edit_files_for(image))
        sessions = session_files_for(image)
        if sessions and not stem_stays(image, images):
            items.extend(sessions)
        for item in items:
            key = os.path.normcase(os.path.normpath(str(item)))
            if key not in seen:
                seen.add(key)
                found.append(item)
    return found


def _session_plan(pairs, *, copy: bool) -> list[tuple[Path, Path, bool, str, str]]:
    """``(session item, where it goes, copy it?, old stem, new stem)`` for the sessions of the photos in ``pairs``."""
    plan: list[tuple[Path, Path, bool, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for old_image, new_image in pairs:
        old, new = Path(old_image), Path(new_image)
        key = (os.path.normcase(str(old.parent)), old.stem.casefold())
        if old == new or key in seen or old.suffix.casefold() not in IMAGE_SUFFIXES:
            continue
        seen.add(key)
        items = session_files_for(old)
        if not items:
            continue
        duplicate = copy or stem_stays(old, [source for source, _target in pairs])
        old_root = edit_root_for(old.parent)
        for item in items:
            folder = edit_root_for(new.parent) if item.parent == old_root else new.parent
            plan.append((item, folder / (new.stem + item.name[len(old.stem):]), duplicate, old.stem, new.stem))
    return plan


def _same(left: Path, right: Path) -> bool:
    try:
        if left.is_dir() != right.is_dir():
            return False
        if not left.is_dir():
            return filecmp.cmp(left, right, shallow=False)
        names = sorted(p.name for p in left.iterdir())
        return names == sorted(p.name for p in right.iterdir()) and all(_same(left / name, right / name) for name in names)
    except OSError:
        return False


def _discard(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError:
        pass


def _holding(final: Path) -> Path:
    """A temporary name beside ``final``, whose folder is made first (hidden, when it is an edit root)."""
    if final.parent.name == EDIT_STORAGE_ROOT_NAME:
        ensure_edit_root(final.parent.parent)
    else:
        final.parent.mkdir(parents=True, exist_ok=True)
    return final.parent / f".carry-{uuid.uuid4().hex}.tmp"


def _duplicate(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def carry_edits(pairs) -> list[str]:
    """Move each photo's edit data from its old path to its new one. ``pairs`` is ``(old_image_path, new_image_path)``.

    Call it after the photos themselves have moved. Returns the names of edit files that could not follow (a file
    with that name is already there, or the move failed); those stay where they were. Never replaces a file.
    Chains and swaps (``a -> b`` while ``b -> a``) are safe: every file is parked under a temporary name first.
    """
    pairs = [(str(old), str(new)) for old, new in pairs]
    # (parked at, original, final, old name, new name, a copy?, a session?)
    parked: list[tuple[Path, Path, Path, str, str, bool, bool]] = []
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
            parked.append((holding, item, final, old.name, new.name, False, False))
    for item, final, duplicate, old_stem, new_stem in _session_plan(pairs, copy=False):
        try:
            holding = _holding(final)
            if duplicate:
                _duplicate(item, holding)
            else:
                shutil.move(str(item), str(holding))
        except OSError:
            failed.append(item.name)
            continue
        parked.append((holding, item, final, old_stem, new_stem, duplicate, True))
    for holding, original, final, old_name, new_name, duplicate, session in parked:
        try:
            if os.path.lexists(final):
                if session and _same(holding, final):
                    _discard(holding)                         # the same session is already there (it was copied, not moved, on the way out)
                    continue
                raise FileExistsError(final.name)
            os.replace(holding, final)
        except OSError:
            failed.append(original.name)
            if duplicate:
                _discard(holding)
                continue
            try:                                              # put it back where it was rather than lose it
                if not os.path.lexists(original):
                    shutil.move(str(holding), str(original))
            except OSError:
                pass
            continue
        _after_placing(final, old_name, new_name, session)
    return failed


def copy_edits(pairs, created: list | None = None) -> list[str]:
    """Give each copied photo a copy of the original's edit data. ``pairs`` is ``(original image path, copy's path)``.

    Call it after the photos themselves have been copied. The original keeps its edit data. Returns the names of edit
    files that could not be copied (a file with that name is already there, or the copy failed). Never replaces a file.
    ``created`` (a list) is given the paths this call made, so undoing the copy can remove those and nothing else.
    """
    pairs = [(str(source), str(copied)) for source, copied in pairs]
    failed: list[str] = []
    todo: list[tuple[Path, Path, str, str, bool]] = []       # (item, final, old name, new name, a session?)
    for source_image, copied_image in pairs:
        source, copied = Path(source_image), Path(copied_image)
        if source == copied:
            continue
        for item in edit_files_for(source):
            todo.append((item, edit_root_for(copied.parent) / (copied.name + item.name[len(source.name):]), source.name, copied.name, False))
    todo.extend((item, final, old_stem, new_stem, True) for item, final, _dup, old_stem, new_stem in _session_plan(pairs, copy=True))
    for item, final, old_name, new_name, session in todo:
        holding = None
        try:
            if os.path.lexists(final):
                raise FileExistsError(final.name)
            root = final.parent if final.parent.name == EDIT_STORAGE_ROOT_NAME else None
            made_root = root is not None and not root.exists()
            holding = _holding(final)
            if made_root and created is not None:
                created.append(str(root))
            _duplicate(item, holding)
            if os.path.lexists(final):
                raise FileExistsError(final.name)
            os.replace(holding, final)
        except OSError:
            failed.append(item.name)
            if holding is not None:
                _discard(holding)
            continue
        if created is not None:
            created.append(str(final))
        _after_placing(final, old_name, new_name, session)
    return failed


def _after_placing(final: Path, old_name: str, new_name: str, session: bool) -> None:
    if old_name == new_name:
        return
    if session:
        if final.name.casefold().endswith(SESSION_SUFFIX):
            _rename_session_assets(final, old_name, new_name)
    elif final.name.casefold().endswith(_MANIFEST_TAIL):
        _rename_manifest_outputs(final, old_name, new_name)


def _rewrite_json(path: Path, change) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not change(data):
            return
        holding = path.with_name(f".carry-{uuid.uuid4().hex}.tmp")
        holding.write_text(json.dumps(data), encoding="utf-8")
        os.replace(holding, path)
    except (OSError, ValueError):
        return                                               # an unreadable file is left as it is


def _rename_session_assets(session: Path, old_stem: str, new_stem: str) -> None:
    """A session names its assets folder; the folder was renamed with the photo's stem, so the name in it follows."""
    def change(data: dict) -> bool:
        assets = data.get("assets")
        if isinstance(assets, dict) and assets.get("dir") == old_stem + SESSION_ASSETS_SUFFIX:
            assets["dir"] = new_stem + SESSION_ASSETS_SUFFIX
            return True
        return False

    _rewrite_json(session, change)


def _rename_manifest_outputs(manifest: Path, old_name: str, new_name: str) -> None:
    """The render manifest names its pictures by file name; they were renamed with the photo, so the names in it follow."""
    def change(data: dict) -> bool:
        outputs = data.get("outputs")
        if not isinstance(outputs, dict):
            return False
        changed = False
        for described in outputs.values():
            name = described.get("file") if isinstance(described, dict) else None
            if isinstance(name, str) and name.casefold().startswith(old_name.casefold()):
                described["file"] = new_name + name[len(old_name):]
                changed = True
        return changed

    _rewrite_json(manifest, change)
