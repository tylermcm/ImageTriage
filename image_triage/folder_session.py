"""Which folder (or virtual scope) is open, and the review session that belongs to it.

These six values used to be plain attributes on ``MainWindow``: 271 reads of ``_current_folder`` alone, from the window
and from five controllers reaching into it. They now live here, with one object as the single writer, so a controller can
hold the session instead of the window (docs/mainwindow_decomposition_plan.md, DC-3.2).

UI-free by design (QtCore only): it is declared so in ``docs/architecture_ratchet.json``.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class FolderSession(QObject):
    """The open folder, its scope, collection mode, browser view mode and decision-store session id.

    ``changed`` carries the name of the field that actually changed (``"folder"``, ``"scope_kind"``, ``"scope_id"``,
    ``"collection_mode"``, ``"session_id"``, ``"browser_view_mode"``); assigning an equal value emits nothing.
    """

    changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._folder = ""
        self._scope_kind = "folder"
        self._scope_id = ""
        self._collection_mode = ""
        self._session_id = ""
        self._browser_view_mode = "grid"

    def _set(self, name: str, attribute: str, value) -> None:
        if getattr(self, attribute) == value:
            return
        setattr(self, attribute, value)
        self.changed.emit(name)

    @property
    def folder(self) -> str:
        """The open folder's path; empty when nothing is open or the scope is virtual."""
        return self._folder

    @folder.setter
    def folder(self, value: str) -> None:
        self._set("folder", "_folder", value)

    @property
    def scope_kind(self) -> str:
        """``"folder"`` for a real folder, otherwise the kind of virtual scope (collection, catalog, ...)."""
        return self._scope_kind

    @scope_kind.setter
    def scope_kind(self, value: str) -> None:
        self._set("scope_kind", "_scope_kind", value)

    @property
    def scope_id(self) -> str:
        return self._scope_id

    @scope_id.setter
    def scope_id(self, value: str) -> None:
        self._set("scope_id", "_scope_id", value)

    @property
    def collection_mode(self) -> str:
        """Empty, or the collection editing mode currently active (``"create"`` / ``"edit"`` / ...)."""
        return self._collection_mode

    @collection_mode.setter
    def collection_mode(self, value: str) -> None:
        self._set("collection_mode", "_collection_mode", value)

    @property
    def session_id(self) -> str:
        """The decision-store session the open folder's marks are written to."""
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self._set("session_id", "_session_id", value)

    @property
    def browser_view_mode(self) -> str:
        """``"grid"`` or ``"details"``."""
        return self._browser_view_mode

    @browser_view_mode.setter
    def browser_view_mode(self, value: str) -> None:
        self._set("browser_view_mode", "_browser_view_mode", value)


def session_field(name: str) -> property:
    """A property for a class that still exposes ``FolderSession.<name>`` under its old attribute name.

    The owner must have a ``_folder_session``. ``MainWindow`` uses it for the transitional shims; a test stub host does
    the same so that unbound ``MainWindow`` methods keep working on it.
    """

    def getter(self):
        return getattr(self._folder_session, name)

    def setter(self, value) -> None:
        setattr(self._folder_session, name, value)

    return property(getter, setter)
