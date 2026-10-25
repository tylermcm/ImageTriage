"""What the person left the interface like, for the next run: the open rail tab, the inspector, the thumbnail size, the sort, the filenames and
decision badges on cards, and which buttons are pinned to the rail.

The Widgets app keeps its own layout in `window/state` and `workspace/toolbar_items` (Qt's saved dock state and its own toolbar names), which mean nothing to
this window, so these are the shell's own keys, `shell/<name>`, in the same settings file; the Widgets app never reads them. Each value is stored as JSON.
The sample page (`--mock`) and the headless renders (`--grab`) keep everything in memory, so they neither read nor write the person's settings.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QObject, Slot

from ..app_identity import user_settings

PREFIX = "shell/"


class UiMemory(QObject):
    def __init__(self, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._memory = memory
        self._store: dict[str, str] = {}
        self._settings = None if memory else user_settings(self)

    @Slot(str, "QVariant", result="QVariant")
    def get(self, name: str, default):
        """The value last saved under `name`, or `default`."""
        raw = self._store.get(name) if self._settings is None else self._settings.value(PREFIX + name, None)
        if not isinstance(raw, str):
            return default
        try:
            return json.loads(raw)
        except ValueError:
            return default

    @Slot(str, "QVariant")
    def set(self, name: str, value) -> None:
        text = json.dumps(value.toVariant() if hasattr(value, "toVariant") else value)
        if self._settings is None:
            self._store[name] = text
        else:
            self._settings.setValue(PREFIX + name, text)
