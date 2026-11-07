"""Safe-trash location, drive classification and restore-manifest storage."""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
from PySide6.QtCore import QStandardPaths
from .file_ops import FileMove, is_unc_path, unc_share_root

class SafeTrash:
    def __init__(self, folder: str):
        self.folder = folder
        self._drive_type_cache = {}

    def folder_drive_root(self, folder: str | None = None) -> str:
        target = folder or self.folder
        if not target:
            return ""
        if is_unc_path(target):
            return unc_share_root(target)
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
        return self.recycle_root_for_folder() / ".image-triage-restore.json"


    def load_recycle_manifest(self) -> dict[str, str]:
        manifest_path = self.recycle_manifest_path()
        if not manifest_path.exists():
            return {}
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


    def save_recycle_manifest(self, data: dict[str, str]) -> None:
        manifest_path = self.recycle_manifest_path()
        if not data:
            if manifest_path.exists():
                manifest_path.unlink()
            return
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


    def remember_recycle_origins(self, moves: tuple[FileMove, ...]) -> None:
        if not any(part.casefold() == "recycle bin" for part in Path(self.folder).parts) and not self.is_temporary_storage_folder():
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
