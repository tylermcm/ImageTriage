from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from .file_ops import create_folder, delete_folder, move_folder, rename_folder
from .scanner import normalized_path_key

if TYPE_CHECKING:
    from .window import MainWindow


class FolderOpsController:
    """Folder create/rename/move/delete prompts and the favorites/recent-folder
    list bookkeeping that follows a folder around when it moves. Kept as a
    thin wrapper around MainWindow (not a fully decoupled service): the
    favorites/recent-destination/recent-folder lists it reads and rewrites
    are shared, UI-visible state that other MainWindow code (the favorites
    panel, recent-folder combos) also owns, so this controller reaches back
    into `window` for them rather than taking a private copy."""

    def __init__(self, window: "MainWindow") -> None:
        self._window = window

    @staticmethod
    def is_filesystem_root(folder: str) -> bool:
        path = Path(folder)
        return str(path.parent) == str(path)

    @staticmethod
    def folder_is_same_or_descendant(path: str, root_folder: str) -> bool:
        try:
            resolved_path = Path(path).resolve(strict=False)
            resolved_root = Path(root_folder).resolve(strict=False)
            resolved_path.relative_to(resolved_root)
            return True
        except ValueError:
            return False

    @classmethod
    def remap_folder_path(cls, path: str, source_root: str, destination_root: str) -> str:
        if not cls.folder_is_same_or_descendant(path, source_root):
            return path
        resolved_path = Path(path).resolve(strict=False)
        resolved_root = Path(source_root).resolve(strict=False)
        relative = resolved_path.relative_to(resolved_root)
        if not relative.parts:
            return destination_root
        return str(Path(destination_root) / relative)

    def remap_folder_references(self, source_root: str, destination_root: str) -> str:
        window = self._window

        favorites: list[str] = []
        seen_favorites: set[str] = set()
        for path in window._favorites:
            mapped = self.remap_folder_path(path, source_root, destination_root)
            if not os.path.isdir(mapped):
                continue
            key = normalized_path_key(mapped)
            if key in seen_favorites:
                continue
            seen_favorites.add(key)
            favorites.append(mapped)
        window._favorites = favorites
        window._save_favorites()
        window._refresh_favorites_panel()

        recent_destinations: list[str] = []
        seen_destinations: set[str] = set()
        for path in window._recent_destinations:
            mapped = self.remap_folder_path(path, source_root, destination_root)
            if not os.path.isdir(mapped):
                continue
            key = normalized_path_key(mapped)
            if key in seen_destinations:
                continue
            seen_destinations.add(key)
            recent_destinations.append(mapped)
        window._recent_destinations = recent_destinations[:10]
        window._save_recent_destinations()

        recent_folders: list[str] = []
        seen_recent_folders: set[str] = set()
        for path in window._recent_folders:
            mapped = self.remap_folder_path(path, source_root, destination_root)
            if not os.path.isdir(mapped):
                continue
            key = normalized_path_key(mapped)
            if key in seen_recent_folders:
                continue
            seen_recent_folders.add(key)
            recent_folders.append(mapped)
        window._recent_folders = recent_folders[:12]
        window._save_recent_folders()
        window._refresh_recent_folder_combos()

        if window._current_folder and self.folder_is_same_or_descendant(window._current_folder, source_root):
            return self.remap_folder_path(window._current_folder, source_root, destination_root)
        return destination_root

    def create_folder_prompt(self, parent_folder: str, *, select_created: bool) -> str | None:
        window = self._window
        folder_name, accepted = QInputDialog.getText(
            window,
            "New Folder",
            "Folder name",
            text="New Folder",
        )
        if not accepted:
            return None
        folder_name = (folder_name or "").strip()
        if not folder_name:
            return None
        try:
            created = create_folder(parent_folder, folder_name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, "Create Folder Failed", f"Could not create folder.\n\n{exc}")
            return None
        window._remember_recent_destination(created)
        window._refresh_folder_tree()
        if select_created:
            window._select_folder(created)
        window.statusBar().showMessage(f"Created folder: {Path(created).name}")
        return created

    def rename_folder(self, folder: str) -> None:
        window = self._window
        current_name = Path(folder).name
        new_name, accepted = QInputDialog.getText(
            window,
            "Rename Folder",
            "Folder name",
            text=current_name,
        )
        if not accepted:
            return
        new_name = (new_name or "").strip()
        if not new_name or new_name == current_name:
            return
        try:
            destination = rename_folder(folder, new_name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, "Rename Failed", f"Could not rename folder.\n\n{exc}")
            return
        target_folder = self.remap_folder_references(folder, destination)
        window._refresh_folder_tree()
        window._select_folder(target_folder if os.path.isdir(target_folder) else destination)
        window.statusBar().showMessage(f"Renamed folder to {new_name}")

    def move_folder_prompt(self, folder: str) -> None:
        window = self._window
        destination_parent = QFileDialog.getExistingDirectory(
            window,
            "Move Folder",
            str(Path(folder).parent),
        )
        if not destination_parent:
            return
        try:
            destination = move_folder(folder, destination_parent)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, "Move Folder Failed", f"Could not move folder.\n\n{exc}")
            return
        target_folder = self.remap_folder_references(folder, destination)
        window._remember_recent_destination(str(Path(destination).parent))
        window._refresh_folder_tree()
        window._select_folder(target_folder if os.path.isdir(target_folder) else destination)
        window.statusBar().showMessage(f"Moved folder to {destination}")

    def delete_folder_prompt(self, folder: str) -> None:
        window = self._window
        if self.is_filesystem_root(folder):
            return
        try:
            has_contents = any(Path(folder).iterdir())
        except OSError as exc:
            QMessageBox.warning(window, "Delete Failed", f"Could not inspect folder.\n\n{exc}")
            return

        message = f"Delete the empty folder '{Path(folder).name}'?"
        if has_contents:
            message = (
                f"Delete the folder '{Path(folder).name}' and everything inside it?\n\n"
                "This will permanently remove all contents."
            )
        confirmation = QMessageBox.question(
            window,
            "Delete Folder",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_folder(folder)
        except OSError as exc:
            QMessageBox.warning(window, "Delete Failed", f"Could not delete folder.\n\n{exc}")
            return

        deleted_key = normalized_path_key(folder)
        window._favorites = [
            path
            for path in window._favorites
            if not (normalized_path_key(path) == deleted_key or normalized_path_key(path).startswith(deleted_key + os.sep))
        ]
        window._save_favorites()
        window._refresh_favorites_panel()
        window._recent_destinations = [
            path
            for path in window._recent_destinations
            if not (normalized_path_key(path) == deleted_key or normalized_path_key(path).startswith(deleted_key + os.sep))
        ]
        window._save_recent_destinations()
        window._recent_folders = [
            path
            for path in window._recent_folders
            if not (normalized_path_key(path) == deleted_key or normalized_path_key(path).startswith(deleted_key + os.sep))
        ]
        window._save_recent_folders()
        window._refresh_recent_folder_combos()

        replacement_folder = str(Path(folder).parent)
        window._refresh_folder_tree()
        if window._current_folder and (
            normalized_path_key(window._current_folder) == deleted_key
            or normalized_path_key(window._current_folder).startswith(deleted_key + os.sep)
        ):
            if os.path.isdir(replacement_folder):
                window._select_folder(replacement_folder)
            else:
                window._current_folder = ""
                window._set_scope_state(kind="folder", scope_id="", label="")
                window._apply_loaded_records([])
        window.statusBar().showMessage(f"Deleted folder: {Path(folder).name}")
