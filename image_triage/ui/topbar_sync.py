"""Keeps the top-bar action buttons in step with their actions."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QToolButton


class _TopbarActionSync(QObject):
    """Keeps one top-bar button in step with the ``QAction`` it was built for.

    It is a child of the button, so the connection to the (long-lived) action
    disappears with the button when the bar is rebuilt. A lambda connected
    straight to ``action.changed`` is never disconnected: it piled up one
    handler per button per rebuild for the life of the app.
    """

    def __init__(
        self,
        sync_button: Callable[[QToolButton, QAction, str], None],
        button: QToolButton,
        action: QAction,
        item_id: str,
    ) -> None:
        super().__init__(button)
        self._sync_button = sync_button
        self._button = button
        self._action = action
        self._item_id = item_id
        action.changed.connect(self.sync)

    @Slot()
    def sync(self) -> None:
        self._sync_button(self._button, self._action, self._item_id)
