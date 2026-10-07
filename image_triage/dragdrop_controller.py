"""Dropping photos onto folders and favorites: drop targets, copy-versus-move, acceptance. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication

from .grid import ThumbnailGridView
from .scanner import normalized_path_key

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class DragDropController(QObject):
    """Dropping photos onto folders and favorites: drop targets, copy-versus-move, acceptance. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window

    def handle_record_drop_event(self, event, *, source: str) -> bool | None:
        event_type = event.type()
        if event_type not in {
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.DragLeave,
            QEvent.Type.Drop,
        }:
            return None
        if self._window._collection_mode:
            event.ignore()
            return True
        if event_type == QEvent.Type.DragLeave:
            return False

        paths = ThumbnailGridView.dragged_record_paths_from_mime(event.mimeData())
        if not paths:
            return None

        point = event.position().toPoint()
        destination_folder = (
            self.folder_drop_target(point)
            if source == "folder_tree"
            else self.favorite_drop_target(point)
        )
        if not destination_folder or not self.can_accept_record_drop(destination_folder):
            event.ignore()
            return True

        copy_requested = self.drag_drop_prefers_copy(event)
        event.setDropAction(Qt.DropAction.CopyAction if copy_requested else Qt.DropAction.MoveAction)
        if event_type == QEvent.Type.Drop:
            event.accept()
            self._window._record_ops.handle_record_drop(paths, destination_folder, copy_requested=copy_requested)
            return True

        event.accept()
        return True

    def folder_drop_target(self, point) -> str:
        index = self._window.folder_tree.indexAt(point)
        if not index.isValid():
            return ""
        folder = self._window.folder_model.filePath(index)
        # Runs on every drag-move over the tree, so it must never ask a share.
        return folder if folder and not self._window._dir_confirmed_missing(folder) else ""

    def favorite_drop_target(self, point) -> str:
        item = self._window.favorites_list.itemAt(point)
        if item is None:
            return ""
        folder = item.data(Qt.ItemDataRole.UserRole)
        return folder if isinstance(folder, str) and folder and not self._window._dir_confirmed_missing(folder) else ""

    def drag_drop_prefers_copy(self, event) -> bool:
        modifiers = QApplication.keyboardModifiers()
        if hasattr(event, "keyboardModifiers"):
            modifiers = event.keyboardModifiers()
        return bool(modifiers & Qt.KeyboardModifier.ControlModifier)

    def can_accept_record_drop(self, destination_folder: str) -> bool:
        if not destination_folder or self._window._dir_confirmed_missing(destination_folder):
            return False
        if not self._window._current_folder:
            return False
        return normalized_path_key(destination_folder) != normalized_path_key(self._window._current_folder)
