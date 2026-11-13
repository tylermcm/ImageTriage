"""Safe-trash location, drive classification, the restore manifest, and listing, restoring and emptying a safe trash."""
from __future__ import annotations
import ctypes
import json
import os
import posixpath
import shutil
from dataclasses import dataclass
from pathlib import Path
from PySide6.QtCore import QStandardPaths
from .file_ops import FileMove, is_unc_path, unc_share_root

MANIFEST_NAME = ".image-triage-restore.json"

# The mount table reader and the classification of a mount as a Windows drive type live in ``path_policy`` (shared with
# the GUI-thread existence policy and the file-name rules); they are used here under the names they always had.
from .path_policy import (  # noqa: E402,F401
    DRIVE_FIXED,
    DRIVE_REMOTE,
    DRIVE_REMOVABLE,
    DRIVE_UNKNOWN,
    _block_device_is_removable,
    _read_mounts,
    classify_mount,
    mount_for_path,
    parse_mountinfo,
)


@dataclass(slots=True, frozen=True)
class SafeTrashItem:
    """One file in a safe trash. ``original_path`` is ``""`` when nothing recorded where it came from."""

    trash_path: str
    original_path: str
    size: int
    modified_ns: int


@dataclass(slots=True, frozen=True)
class SafeTrashOutcome:
    """What a restore or an empty did: the trash paths it handled, and ``(trash path, why)`` for each one it left."""

    done: tuple[str, ...]
    left: tuple[tuple[str, str], ...]


class SafeTrash:
    def __init__(self, folder: str):
        self.folder = folder
        self._drive_type_cache = {}
        self._mounts: list[tuple[str, str, str]] | None = None

    def _posix_mount(self, path: str) -> tuple[str, str, str] | None:
        if self._mounts is None:
            self._mounts = _read_mounts()
        return mount_for_path(path, self._mounts)

    def folder_drive_root(self, folder: str | None = None) -> str:
        target = folder or self.folder
        if not target:
            return ""
        if is_unc_path(target):
            return unc_share_root(target)
        if os.name != "nt":
            # There are no drive letters: the "drive" a folder is on is where its filesystem is mounted.
            mount = self._posix_mount(target)
            if mount is not None:
                return mount[0]
        try:
            return Path(target).anchor
        except (OSError, ValueError):
            return ""


    def drive_type(self, root: str) -> int:
        if not root:
            return 0
        if is_unc_path(root):
            return 4
        cache_key = os.path.normpath(root).casefold()
        cached = self._drive_type_cache.get(cache_key)
        if cached is not None:
            return cached
        if os.name != "nt":
            drive_type = classify_mount(self._posix_mount(root), removable=_block_device_is_removable)
        else:
            try:
                drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(str(root)))
            except Exception:
                drive_type = 0
        self._drive_type_cache[cache_key] = drive_type
        return drive_type


    def is_temporary_storage_folder(self, folder: str | None = None) -> bool:
        return self.drive_type(self.folder_drive_root(folder)) == 2


    def is_slow_source_folder(self, folder: str | None = None) -> bool:
        drive_type = self.drive_type(self.folder_drive_root(folder))
        return drive_type in {2, 4}


    def recycle_root_for_folder(self, folder: str | None = None) -> Path:
        target_folder = folder or self.folder
        if target_folder:
            target_path = Path(target_folder)
            recycle_parts: list[str] = []
            for part in target_path.parts:
                recycle_parts.append(part)
                if part.casefold() == "recycle bin":
                    return Path(*recycle_parts)
        if self.is_temporary_storage_folder(target_folder):
            base_folder = Path(target_folder) if target_folder else Path(self.folder_drive_root())
            parent_folder = base_folder.parent if base_folder.parent != base_folder else base_folder
            return parent_folder / "recycle bin"
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        root = Path(app_data) if app_data else Path.home() / ".image-triage"
        return root / "safe-trash"


    def recycle_manifest_path(self) -> Path:
        return self.recycle_root_for_folder() / MANIFEST_NAME


    def load_recycle_manifest(self) -> dict[str, str]:
        manifest_path = self.recycle_manifest_path()
        if not manifest_path.exists():
            return {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items() if isinstance(key, str) and isinstance(value, str)}


    def save_recycle_manifest(self, data: dict[str, str]) -> None:
        manifest_path = self.recycle_manifest_path()
        if not data:
            if manifest_path.exists():
                manifest_path.unlink()
            return
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside and swapped in: a crash half way through must not leave a manifest that cannot be read, since it is
        # the only record of where the files in the trash came from.
        holding = manifest_path.with_name(manifest_path.name + ".tmp")
        holding.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(holding, manifest_path)


    def remember_recycle_origins(self, moves: tuple[FileMove, ...]) -> None:
        """Record where each trashed file came from. Every safe trash keeps this, wherever it is: without it a file in the trash
        cannot be put back once the session's undo list is gone."""
        if not moves:
            return
        manifest = self.load_recycle_manifest()
        for move in moves:
            manifest[move.target_path] = move.source_path
        self.save_recycle_manifest(manifest)


    def forget_recycle_origins(self, paths: tuple[str, ...]) -> None:
        manifest = self.load_recycle_manifest()
        changed = False
        for path in paths:
            if path in manifest:
                manifest.pop(path, None)
                changed = True
        if changed:
            self.save_recycle_manifest(manifest)


    # ---------------- what is in the trash, putting it back, emptying it ----------------
    def list_items(self) -> list[SafeTrashItem]:
        """Every file in this safe trash, newest first, with where it came from when that was recorded."""
        root = self.recycle_root_for_folder()
        manifest = {_trash_key(path): origin for path, origin in self.load_recycle_manifest().items()}
        items: list[SafeTrashItem] = []
        try:
            entries = list(os.scandir(root))
        except OSError:
            return items
        for entry in entries:
            try:
                if entry.name.startswith(MANIFEST_NAME) or not entry.is_file(follow_symlinks=False):
                    continue
                stat_result = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            items.append(SafeTrashItem(
                trash_path=entry.path,
                original_path=manifest.get(_trash_key(entry.path), ""),
                size=int(stat_result.st_size),
                modified_ns=int(stat_result.st_mtime_ns),
            ))
        items.sort(key=lambda item: (-item.modified_ns, item.trash_path))
        return items

    def restore(self, trash_paths=None) -> SafeTrashOutcome:
        """Put files back where they came from (all of them, or the given trash paths). A file is left in the trash, with the
        reason, when its origin was not recorded or when something now has its old name: nothing is ever replaced."""
        wanted = None if trash_paths is None else {_trash_key(path) for path in trash_paths}
        done: list[str] = []
        left: list[tuple[str, str]] = []
        for item in self.list_items():
            if wanted is not None and _trash_key(item.trash_path) not in wanted:
                continue
            if not item.original_path:
                left.append((item.trash_path, "where it came from was not recorded"))
                continue
            if os.path.lexists(item.original_path):
                left.append((item.trash_path, f"a file named {Path(item.original_path).name} is in its folder now"))
                continue
            try:
                Path(item.original_path).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(item.trash_path, item.original_path)
            except OSError as exc:
                left.append((item.trash_path, str(exc)))
                continue
            done.append(item.trash_path)
        self._forget_keys({_trash_key(path) for path in done})
        return SafeTrashOutcome(tuple(done), tuple(left))

    def empty(self, trash_paths=None) -> SafeTrashOutcome:
        """Delete files in this safe trash for good (all of them, or the given trash paths). Only files directly in the trash
        folder are touched; a path anywhere else is refused."""
        wanted = None if trash_paths is None else {_trash_key(path) for path in trash_paths}
        done: list[str] = []
        left: list[tuple[str, str]] = []
        for item in self.list_items():
            if wanted is not None and _trash_key(item.trash_path) not in wanted:
                continue
            try:
                os.remove(item.trash_path)
            except OSError as exc:
                left.append((item.trash_path, str(exc)))
                continue
            done.append(item.trash_path)
        self._forget_keys({_trash_key(path) for path in done})
        return SafeTrashOutcome(tuple(done), tuple(left))

    def _forget_keys(self, keys: set[str]) -> None:
        if not keys:
            return
        manifest = self.load_recycle_manifest()
        kept = {path: origin for path, origin in manifest.items() if _trash_key(path) not in keys}
        if len(kept) != len(manifest):
            self.save_recycle_manifest(kept)


def _trash_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))
