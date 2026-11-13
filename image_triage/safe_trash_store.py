"""What is in a safe trash, photo by photo: listing it, putting photos back, deleting them for good, and keeping it under a size limit.

``safe_trash.SafeTrash`` says where a folder's trash is and keeps the manifest of where each file came from. A deleted photo is
several files (the RAW, its JPEG, sidecars, edit data), so this module groups them again and treats each photo as one thing:

* beside the manifest it keeps a second record, ``PHOTOS_NAME``, with one entry per deleted photo: its files in the trash, when
  it was deleted, and the decision it had (a rating, winner or reject) with the profile that decision was saved under, so a
  photo restored long after the session's Undo is gone gets its decision back;
* files in the trash that no entry covers (deleted by an older build, or by a crash between the move and the record) are
  grouped by the folder and name they came from; a file whose origin was never recorded is listed on its own;
* restoring a photo is all or nothing and never replaces a file that now has the old name (``restore_moves``);
* a photo whose folder no longer exists is not restored into a folder made up for it: it is reported, and can be restored into a
  folder the person chooses, where it is renamed as a bundle if its name is taken there.

Nothing here uses Qt beyond what ``safe_trash`` already does, and every function may be called from a worker thread.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .edit_storage import ensure_edit_root
from .file_ops import FileMove, bundle_destinations
from .safe_trash import MANIFEST_NAME
from .scanner import APP_RECYCLE_DIRECTORY_NAME, EDIT_STORAGE_ROOT_NAME, IMAGE_SUFFIXES

PHOTOS_NAME = MANIFEST_NAME + ".photos"          # starts with the manifest's name, so the trash's own listing passes over it
_QUICK_THUMB_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"})


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


@dataclass(slots=True)
class TrashEntry:
    """One deleted photo. ``files`` is ``(path in the trash, where it came from or "")`` with the photo itself first."""

    id: str
    root: str
    name: str
    files: tuple[tuple[str, str], ...]
    size: int
    deleted: float                                # seconds since the epoch; 0 when not known
    session: str = ""
    annotation: dict | None = None
    recorded: bool = field(default=True)          # False: put together from the manifest, no entry was kept for it

    @property
    def original(self) -> str:
        return self.files[0][1] if self.files else ""

    @property
    def folder(self) -> str:
        return photo_folder(self.original) if self.original else ""

    @property
    def trash_paths(self) -> tuple[str, ...]:
        return tuple(path for path, _origin in self.files)

    @property
    def thumbnail(self) -> str:
        """A file of this photo that is cheap to show as it is (a JPEG or PNG), or ""."""
        for path, _origin in self.files:
            if Path(path).suffix.casefold() in _QUICK_THUMB_SUFFIXES and os.path.isfile(path):
                return path
        return ""


def photo_folder(original_path: str) -> str:
    """The folder a file belongs to: its own, or the photo's when the file was edit data in the hidden edit folder."""
    parent = Path(original_path).parent
    return str(parent.parent if parent.name == EDIT_STORAGE_ROOT_NAME else parent)


# ---------------- the two records in a trash folder ----------------
def _read_json(path: Path, kind):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return kind()
    return data if isinstance(data, kind) else kind()


def _write_json(path: Path, data) -> None:
    if not data:
        try:
            path.unlink()
        except OSError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    holding = path.with_name(path.name + ".tmp")
    holding.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(holding, path)


def load_manifest(root: str | Path) -> dict[str, str]:
    data = _read_json(Path(root) / MANIFEST_NAME, dict)
    return {str(k): str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def load_photos(root: str | Path) -> list[dict]:
    return [item for item in _read_json(Path(root) / PHOTOS_NAME, list) if isinstance(item, dict) and isinstance(item.get("files"), list)]


def annotation_to_dict(annotation) -> dict | None:
    if annotation is None or annotation.is_empty:
        return None
    return {"winner": bool(annotation.winner), "reject": bool(annotation.reject), "photoshop": bool(annotation.photoshop),
            "rating": int(annotation.rating), "tags": list(annotation.tags), "review_round": str(annotation.review_round)}


def annotation_from_dict(data):
    from .models import SessionAnnotation

    if not isinstance(data, dict):
        return None
    try:
        return SessionAnnotation(winner=bool(data.get("winner")), reject=bool(data.get("reject")), photoshop=bool(data.get("photoshop")),
                                 rating=max(0, min(5, int(data.get("rating") or 0))), tags=tuple(str(t) for t in data.get("tags") or ()),
                                 review_round=str(data.get("review_round") or ""))
    except (TypeError, ValueError):
        return None


def remember_photos(root: str | Path, photos) -> None:
    """Record deleted photos. ``photos`` is ``[(name, [paths in the trash, the photo first], annotation, session)]``."""
    photos = list(photos)
    if not photos:
        return
    known = load_photos(root)
    now = time.time()
    for name, files, annotation, session in photos:
        known.append({"id": uuid.uuid4().hex, "name": str(name), "files": [str(path) for path in files], "deleted": now,
                      "session": str(session or ""), "annotation": annotation_to_dict(annotation)})
    _write_json(Path(root) / PHOTOS_NAME, known)


def forget_files(root: str | Path, trash_paths) -> None:
    """These files have left the trash (restored, or deleted for good): drop them from both records."""
    gone = {_key(path) for path in trash_paths}
    if not gone:
        return
    manifest = load_manifest(root)
    kept = {path: origin for path, origin in manifest.items() if _key(path) not in gone}
    if len(kept) != len(manifest):
        _write_json(Path(root) / MANIFEST_NAME, kept)
    photos = load_photos(root)
    left = []
    for item in photos:
        files = [path for path in item["files"] if _key(path) not in gone]
        if files:
            left.append(dict(item, files=files))
    if left != photos:
        _write_json(Path(root) / PHOTOS_NAME, left)


# ---------------- listing ----------------
def _tree_size(path: str) -> int:
    total = 0
    try:
        if not os.path.isdir(path) or os.path.islink(path):
            return os.lstat(path).st_size
        for folder, _dirs, names in os.walk(path):
            for name in names:
                try:
                    total += os.lstat(os.path.join(folder, name)).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _is_primary(path: str) -> bool:
    return Path(path).suffix.casefold() in IMAGE_SUFFIXES


def list_entries(root: str | Path) -> list[TrashEntry]:
    """The photos in this trash, the most recently deleted first."""
    root = str(root)
    present: dict[str, tuple[str, float]] = {}                # key -> (path, when it changed last)
    try:
        with os.scandir(root) as listing:
            for item in listing:
                if item.name.startswith(MANIFEST_NAME) or item.name.startswith(".carry-"):
                    continue
                try:
                    present[_key(item.path)] = (item.path, item.stat(follow_symlinks=False).st_ctime)
                except OSError:
                    continue
    except OSError:
        return []
    origins = {_key(path): origin for path, origin in load_manifest(root).items()}
    entries: list[TrashEntry] = []
    used: set[str] = set()
    for item in load_photos(root):
        files = [present[_key(path)][0] for path in item["files"] if _key(path) in present and _key(path) not in used]
        if not files:
            continue
        used.update(_key(path) for path in files)
        entries.append(_entry(root, str(item.get("id") or uuid.uuid4().hex), str(item.get("name") or ""), files, origins,
                              float(item.get("deleted") or 0), str(item.get("session") or ""), item.get("annotation"), True))
    groups: dict[tuple[str, str], list[str]] = {}
    for key, (path, _changed) in sorted(present.items()):
        if key in used:
            continue
        origin = origins.get(key, "")
        # files that came from one folder under one name (IMG_1.NEF, IMG_1.JPG, IMG_1.xmp, the edit folder's IMG_1.NEF.cameraraw.json) are one photo
        group = (_key(photo_folder(origin)), Path(origin).name.split(".", 1)[0].casefold()) if origin else ("", key)
        groups.setdefault(group, []).append(path)
    for (_folder, _stem), files in groups.items():
        deleted = max(present[_key(path)][1] for path in files)
        entries.append(_entry(root, "f-" + uuid.uuid5(uuid.NAMESPACE_URL, _key(files[0])).hex, "", files, origins, deleted, "", None, False))
    entries.sort(key=lambda entry: (-entry.deleted, entry.name.casefold()))
    return entries


def _entry(root, ident, name, files, origins, deleted, session, annotation, recorded) -> TrashEntry:
    # a recorded photo's files are already in order, the photo first; otherwise the image comes first, a RAW before its JPEG
    ordered = list(files) if recorded else sorted(files, key=lambda path: (not _is_primary(origins.get(_key(path), "") or path),
                                                                           Path(path).suffix.casefold() in _QUICK_THUMB_SUFFIXES, path))
    pairs = tuple((path, origins.get(_key(path), "")) for path in ordered)
    shown = name or Path(pairs[0][1] or pairs[0][0]).name
    return TrashEntry(id=ident, root=root, name=shown, files=pairs, size=sum(_tree_size(path) for path in ordered), deleted=deleted,
                      session=session, annotation=annotation if isinstance(annotation, dict) else None, recorded=recorded)


def total_size(entries) -> int:
    return sum(entry.size for entry in entries)


# ---------------- putting back ----------------
def restore_moves(moves, gone: str = "is no longer there") -> None:
    """Take one photo's files back to their places (``source_path``) from where they are (``target_path``), all of them or none.
    Never replaces a file that is there now, and can be run again after a partial failure (a file already back is passed over)."""
    todo = []
    for move in moves:
        if not os.path.lexists(move.target_path):
            if os.path.lexists(move.source_path):
                continue                                  # already back from an earlier attempt
            raise OSError(f"{Path(move.target_path).name} {gone}")
        if os.path.lexists(move.source_path):
            raise OSError(f"A file named {Path(move.source_path).name} is already in the original folder")
        todo.append(move)
    back = []
    try:
        for move in todo:
            parent = Path(move.source_path).parent
            if parent.name == EDIT_STORAGE_ROOT_NAME:
                ensure_edit_root(parent.parent)
            else:
                parent.mkdir(parents=True, exist_ok=True)
            shutil.move(move.target_path, move.source_path)
            back.append(move)
    except OSError:
        for move in reversed(back):                       # the bundle stays together: what went back returns to where it was taken from
            try:
                if not os.path.lexists(move.target_path):
                    shutil.move(move.source_path, move.target_path)
            except OSError:
                pass
        raise


def _targets_in(entry: TrashEntry, folder: str) -> list[str]:
    """Where each of the photo's files goes when it is restored into ``folder`` instead of where it came from: the photo and
    its sidecars into the folder (renamed together if a name is taken), its edit data into the folder's edit folder under the
    photo's new name."""
    plain = [(path, origin) for path, origin in entry.files if not origin or Path(origin).parent.name != EDIT_STORAGE_ROOT_NAME]
    names = tuple(origin or path for path, origin in plain)
    placed = dict(zip((path for path, _origin in plain), bundle_destinations(names, folder)))
    renamed = {Path(origin or path).name: Path(placed[path]).name for path, origin in plain}          # old name -> new name
    targets = []
    for path, origin in entry.files:
        if path in placed:
            targets.append(placed[path])
            continue
        old = Path(origin).name
        owner = max((name for name in renamed if old.casefold().startswith(name.casefold())), key=len, default="")
        if owner:
            new = renamed[owner] + old[len(owner):]
        else:                                             # keyed by the stem (a built-in editor session)
            stems = {Path(name).stem: Path(new_name).stem for name, new_name in renamed.items()}
            stem = max((s for s in stems if old.casefold().startswith(s.casefold())), key=len, default="")
            new = stems[stem] + old[len(stem):] if stem else old
        targets.append(str(Path(folder) / EDIT_STORAGE_ROOT_NAME / new))
    return targets


def restore_entries(entries, ctx=None, to_folder: str = "") -> dict:
    """Put photos back where they came from, or into ``to_folder``. Returns ``done`` as ``[(entry, {where a file came from: where
    it is now})]``, ``failed`` as ``[(entry, why)]`` and ``missing`` (the entries, also in ``failed``, whose folder is gone)."""
    done, failed, missing = [], [], []
    entries = list(entries)
    for index, entry in enumerate(entries):
        if ctx is not None:
            if ctx.cancelled:
                break
            ctx.step(index, entry.name)
        try:
            if to_folder:
                targets = _targets_in(entry, to_folder)
            else:
                if any(not origin for _path, origin in entry.files):
                    raise LookupError("where it came from was not recorded")
                folders = {photo_folder(origin) for _path, origin in entry.files}
                gone = sorted(folder for folder in folders if not os.path.isdir(folder))
                if gone:
                    missing.append(entry)
                    raise LookupError(f"its folder is no longer there ({gone[0]})")
                targets = [origin for _path, origin in entry.files]
            restore_moves([FileMove(source_path=target, target_path=path) for (path, _origin), target in zip(entry.files, targets)],
                          gone="is no longer in the safe trash")
        except (OSError, LookupError) as exc:
            failed.append((entry, str(exc)))
            continue
        forget_files(entry.root, entry.trash_paths)
        done.append((entry, {origin or path: target for (path, origin), target in zip(entry.files, targets)}))
    if ctx is not None:
        ctx.step(len(entries), "", len(entries))
    return {"done": done, "failed": failed, "missing": missing, "cancelled": bool(ctx is not None and ctx.cancelled and len(done) + len(failed) < len(entries))}


# ---------------- deleting for good, and the size limit ----------------
def _delete(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def purge_entries(entries, ctx=None) -> dict:
    """Delete photos in a trash for good. Only what is directly in the entry's own trash folder is touched."""
    done, failed = [], []
    entries = list(entries)
    for index, entry in enumerate(entries):
        if ctx is not None:
            if ctx.cancelled:
                break
            ctx.step(index, entry.name)
        removed = []
        try:
            for path in entry.trash_paths:
                if _key(str(Path(path).parent)) != _key(entry.root):
                    raise OSError(f"{Path(path).name} is not in the safe trash")
                _delete(path)
                removed.append(path)
        except OSError as exc:
            failed.append((entry, str(exc)))
        else:
            done.append(entry)
        forget_files(entry.root, removed)
    if ctx is not None:
        ctx.step(len(entries), "", len(entries))
    return {"done": done, "failed": failed, "cancelled": bool(ctx is not None and ctx.cancelled and len(done) + len(failed) < len(entries))}


def over_limit(entries, limit_bytes: int, protect=()) -> list[TrashEntry]:
    """The photos a size limit removes: the ones deleted longest ago, whole photos, until what is left fits. The photos
    holding a path in ``protect`` (the delete that has just been made) are never chosen."""
    if limit_bytes <= 0:
        return []
    kept = {_key(path) for path in protect}
    total = total_size(entries)
    chosen = []
    for entry in sorted(entries, key=lambda item: (item.deleted, item.name.casefold())):
        if total <= limit_bytes:
            break
        if any(_key(path) in kept for path in entry.trash_paths):
            continue
        chosen.append(entry)
        total -= entry.size
    return chosen


def enforce_limit(root: str | Path, limit_bytes: int, protect=()) -> dict:
    """Bring this trash under ``limit_bytes`` (0: no limit) by deleting the photos deleted longest ago for good."""
    if limit_bytes <= 0:
        return {"done": [], "failed": [], "cancelled": False}
    return purge_entries(over_limit(list_entries(root), limit_bytes, protect))


# ---------------- which trash folders there are ----------------
def removable_trash_roots(volumes, is_removable, depth: int = 2) -> list[str]:
    """The app's recycle folders on the mounted removable drives. The app makes one beside the folder a photo was deleted from,
    so they are looked for in each drive's top folder and ``depth`` levels below (a card's DCIM, and the folders in it)."""
    found: list[str] = []
    for volume in volumes:
        if not is_removable(volume):
            continue
        level = [str(volume)]
        for _ in range(depth + 1):
            below: list[str] = []
            for folder in level:
                try:
                    with os.scandir(folder) as listing:
                        for item in listing:
                            try:
                                if not item.is_dir(follow_symlinks=False):
                                    continue
                            except OSError:
                                continue
                            if item.name.casefold() == APP_RECYCLE_DIRECTORY_NAME:
                                found.append(item.path)
                            elif not item.name.startswith((".", "$")):
                                below.append(item.path)
                except OSError:
                    continue
            level = below
    return found
