"""The Move / Copy To Folder cards and the handoff's progress, for the QML shell.

Wording follows the mockup (and the Widgets app's `ui/transfer_dialogs.py`): "Copy 12 items?", "Copying 12 items", "Copied 12 items".
**Nothing is transferred.** `start()` plays a scripted progress so the cards can be seen and checked; no file is touched. See
docs/qml_unwired.md.
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

_STEP = 0.018                 # of the whole per tick
_TICK_MS = 110
_SAMPLE_BYTES = int(1.4 * 1024 ** 3)


def _size(n: float) -> str:
    if n < 1048576:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1048576:.0f} MB" if n < 1024 ** 3 else f"{n / 1024 ** 3:.1f} GB"


class TransferModel(QObject):
    cardChanged = Signal()

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._memory = memory               # --mock and tests: the sample size (1.4 GB) instead of the files' own
        self._state = ""            # "", "confirm", "progress", "done"
        self._kind = "copy"         # copy, move, export, archive; "wf" while asking about a destructive workflow
        self._move = False
        self._count = 0
        self._from = ""
        self._to = ""
        self._comp = True
        self._p = 0.0
        self._cancel = False
        self._total = _SAMPLE_BYTES
        self._names: list[str] = []
        self._card: dict = {}
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        photos.countsChanged.connect(self._photos_changed)

    def _photos_changed(self) -> None:
        if self._state:
            self._names = []
            self._rebuild()

    # -- entry points -------------------------------------------------------------------------------------------------------------
    @Slot(str, int, str, str)
    def ask(self, kind: str, count: int, from_dir: str, to_dir: str) -> None:
        """Move / Copy To Folder: confirm first."""
        self._begin(kind, count, from_dir, to_dir)
        self._state = "confirm"
        self._rebuild()

    @Slot(str, int, str, str)
    def handoff(self, kind: str, count: int, from_dir: str, to_dir: str) -> None:
        """A handoff recipe was run: a recipe that moves bundles asks first, anything else starts at once."""
        self._begin(kind, count, from_dir, to_dir)
        if kind == "move":
            self._kind = "wf"
            self._state = "confirm"
            self._rebuild()
        else:
            self._start()

    def _scan(self) -> None:
        """File names and total size from the photos the grid shows (the sample 1.4 GB when the files are not there)."""
        total, names = 0, []
        for index in range(min(self._count, self._photos.shown)):
            row = self._photos.get(index)
            names.append(str(row.get("name", "")) + ("." + str(row.get("ext", "")) if row.get("ext") else ""))
            try:
                total += os.path.getsize(row.get("path", ""))
            except OSError:
                total = 0
                break
        self._names = [n for n in names if n]
        self._total = _SAMPLE_BYTES if self._memory else (total or _SAMPLE_BYTES)

    def _begin(self, kind: str, count: int, from_dir: str, to_dir: str) -> None:
        self._timer.stop()
        self._kind, self._move = kind, kind == "move"
        self._count, self._from, self._to = max(1, count), from_dir, to_dir
        self._p, self._cancel, self._comp = 0.0, False, True
        self._names = []

    def _start(self) -> None:
        self._state, self._p, self._cancel = "progress", 0.0, False
        self._timer.start()
        self._rebuild()

    # -- buttons --------------------------------------------------------------------------------------------------------------------
    @Slot()
    def confirm(self) -> None:
        if self._state != "confirm":
            return
        if self._kind == "wf":
            self._kind, self._move = "move", True
        self._start()

    @Slot()
    def dismiss(self) -> None:
        self._timer.stop()
        self._state = ""
        self._rebuild()

    @Slot()
    def cancel(self) -> None:
        if self._state == "progress":
            self._cancel = True
            self._rebuild()

    @Slot(bool)
    def setCompanions(self, on: bool) -> None:
        self._comp = on
        self._rebuild()

    @Slot()
    def openDestination(self) -> None:
        if self._to and os.path.isdir(self._to):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._to))
        self.dismiss()

    @Slot(str, str, int, float)
    def show(self, state: str, kind: str, count: int, progress: float) -> None:
        """Test hook: put the cards in a state (`--ui txState=progress;txKind=copy;txP=0.42`)."""
        self._begin(kind if kind != "wf" else "move", count, self._photos.folderPath or "D:\\Photos\\2026\\Georgia trip\\Kutaisi",
                    "E:\\Cards\\Kutaisi, Georgia")
        if kind == "wf":
            self._kind = "wf"
        self._state, self._p = state, progress
        self._timer.stop()
        self._rebuild()

    def _tick(self) -> None:
        if self._cancel:
            self.dismiss()
            return
        self._p = min(1.0, self._p + _STEP)
        if self._p >= 1.0:
            self._timer.stop()
            self._state = "done"
        self._rebuild()

    # -- the card -----------------------------------------------------------------------------------------------------------------------
    def _rebuild(self) -> None:
        if not self._names:
            self._scan()
        verb = {"export": "Exporting", "archive": "Archiving", "move": "Moving"}.get(self._kind, "Copying")
        past = {"export": "Exported", "archive": "Archived", "move": "Moved"}.get(self._kind, "Copied")
        imperative = "Move" if self._move or self._kind == "move" else "Copy"
        n = self._count
        items = f"{n} item{'s' if n != 1 else ''}"
        images = f"{n} image{'s' if n != 1 else ''}"
        left_frac = max(0.0, 1.0 - self._p)
        done_items = math.floor(n * self._p)
        seconds = max(0, round(left_frac * 6))
        wf = self._kind == "wf"
        self._card = {
            "state": self._state,
            "title": "Run destructive workflow?" if wf else f"{imperative} {items}?",
            "body": "This workflow moves the source bundles into the destination structure. Continue?" if wf else f"{imperative} {images} to:",
            "hasPath": not wf, "path": self._to, "hasCompanions": not wf, "companions": self._comp,
            "yes": "Move bundles" if wf else imperative, "danger": wf,
            "headline": f"{verb} {items}", "route": f"{self._from} \u2192 {self._to}",
            "pct": round(self._p * 100), "pctText": f"{round(self._p * 100)}% complete",
            "speed": f"{78 + 9 * math.sin(self._p * 14):.1f} MB/s" if 0 < self._p < 1 else "",
            "name": self._names[min(len(self._names) - 1, done_items)] if self._names else "",
            "eta": "Less than a second" if seconds < 2 else f"About {seconds} seconds",
            "left": f"{n - done_items} ({_size(self._total * left_frac)})",
            "cancelLabel": "Cancelling\u2026" if self._cancel else "Cancel",
            "doneTitle": f"{past} {items}", "doneBody": f"{past} {images} to:",
        }
        self.cardChanged.emit()

    card = Property("QVariantMap", lambda self: self._card, notify=cardChanged)
    active = Property(bool, lambda self: self._state != "", notify=cardChanged)
