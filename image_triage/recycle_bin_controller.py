from __future__ import annotations

import ctypes
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QFileDialog, QMessageBox

from .file_ops import FileMove, is_unc_path, unc_share_root
from .folder_session import FolderSession

if TYPE_CHECKING:
    from .window import MainWindow


class RecycleBinController:
    """Drive-type detection, the per-drive/removable-media "recycle bin" (not
    the OS trash - see `_trash_or_delete_paths` for that), and its restore
    manifest. Owns `_drive_type_cache`; everything else it touches (the
    empty-recycle-bin action, `_is_recycle_folder`, the current folder) is
    shared MainWindow state reached through the `window` back-reference,
    since many callers elsewhere in file-move/delete/undo code depend on
    this controller's read-only classifiers and manifest bookkeeping."""

    @property
    def _session(self) -> FolderSession:
        return self._window._folder_session

    def __init__(self, window: "MainWindow") -> None:
        self._window = window
        self._drive_type_cache: dict[str, int] = {}

    def folder_drive_root(self, folder: str | None = None) -> str:
        target = folder or self._session.folder
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
        target_folder = folder or self._session.folder
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

    def refresh_recycle_button(self, *, update_action_states: bool = True) -> None:
        window = self._window
        if window.actions is None:
            return
        if window._scan_in_progress:
            window.actions.empty_recycle_bin.setEnabled(False)
            window.actions.empty_recycle_bin.setToolTip("Available after the folder finishes loading.")
            if update_action_states:
                window._update_action_states()
            return
        if self.is_temporary_storage_folder():
            recycle_root = self.recycle_root_for_folder()
            has_contents = recycle_root.exists() and any(recycle_root.iterdir())
            window.actions.empty_recycle_bin.setEnabled(has_contents)
            window.actions.empty_recycle_bin.setToolTip(
                "Permanently delete everything in this folder's local recycle bin."
            )
            if update_action_states:
                window._update_action_states()
            return
        window.actions.empty_recycle_bin.setEnabled(False)
        window.actions.empty_recycle_bin.setToolTip(
            "Available when browsing a removable drive with items in its Image Triage recycle folder."
        )
        if update_action_states:
            window._update_action_states()

    def empty_recycle_bin(self) -> None:
        window = self._window
        recycle_root = self.recycle_root_for_folder()
        if not self.is_temporary_storage_folder():
            window.statusBar().showMessage("Open a removable-drive folder to empty its recycle bin")
            return
        if not recycle_root.exists() or not any(recycle_root.iterdir()):
            self.refresh_recycle_button()
            window.statusBar().showMessage("Recycle bin is already empty")
            return

        confirmation = QMessageBox.warning(
            window,
            "Empty Recycle Bin?",
            (
                "This will permanently delete everything currently stored in this drive's "
                "local recycle bin.\n\nThis action cannot be undone."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        shutil.rmtree(recycle_root, ignore_errors=False)
        self.refresh_recycle_button()
        window.statusBar().showMessage(f"Emptied recycle bin for {self._session.folder}")

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
        window = self._window
        if not window._is_recycle_folder() and not self.is_temporary_storage_folder():
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

    def restore_bundle(self, recycle_paths: tuple[str, ...]) -> tuple[FileMove, ...]:
        window = self._window
        manifest = self.load_recycle_manifest()
        restores: list[FileMove] = []
        restored_targets: list[FileMove] = []
        destination_dir: str | None = None
        recycle_root = self.recycle_root_for_folder()
        restore_root = recycle_root.parent
        try:
            for recycle_path in recycle_paths:
                original_path = manifest.get(recycle_path)
                if not original_path:
                    normalized_recycle = os.path.normcase(os.path.normpath(recycle_path))
                    for stored_path, stored_original in manifest.items():
                        if os.path.normcase(os.path.normpath(stored_path)) == normalized_recycle:
                            original_path = stored_original
                            break
                if not original_path:
                    recycle_file = Path(recycle_path)
                    try:
                        relative_path = recycle_file.relative_to(recycle_root)
                        if len(relative_path.parts) > 1:
                            inferred_original = restore_root / relative_path
                            destination = window._unique_destination(str(inferred_original.parent), inferred_original.name)
                        else:
                            if destination_dir is None:
                                destination_dir = QFileDialog.getExistingDirectory(
                                    window,
                                    "Choose Restore Folder",
                                    str(restore_root),
                                )
                                if not destination_dir:
                                    raise OSError("Restore was cancelled.")
                            destination = window._unique_destination(destination_dir, recycle_file.name)
                    except ValueError:
                        if destination_dir is None:
                            destination_dir = QFileDialog.getExistingDirectory(
                                window,
                                "Choose Restore Folder",
                                str(restore_root),
                            )
                            if not destination_dir:
                                raise OSError("Restore was cancelled.")
                        destination = window._unique_destination(destination_dir, Path(recycle_path).name)
                else:
                    destination_dir = str(Path(original_path).parent)
                    destination = window._unique_destination(destination_dir, Path(original_path).name)
                Path(destination).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(recycle_path, destination)
                file_move = FileMove(source_path=destination, target_path=recycle_path)
                restores.append(file_move)
                restored_targets.append(file_move)
        except OSError as exc:
            for restored in reversed(restored_targets):
                if os.path.exists(restored.source_path):
                    Path(restored.target_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(restored.source_path, restored.target_path)
            raise exc
        self.forget_recycle_origins(recycle_paths)
        return tuple(restores)
