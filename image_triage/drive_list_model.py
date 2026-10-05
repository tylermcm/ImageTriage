"""The model behind the Drives list: one row per drive letter, built without asking any drive anything on the GUI thread.

Why this is not a ``QFileSystemModel`` any more: a ``QFileSystemModel`` rooted at "all drives" asks Windows about every
drive on its single worker thread, and asking about a mapped network drive whose machine is off takes ~20 s per query.
One dead ``P:`` therefore emptied the Drives list, held every folder listing back for 20 s, and froze the window whenever
the list painted a usage bar. Here

* the rows come from the drive-letter bitmask and ``GetDriveTypeW`` (:func:`path_policy.drive_roots`), which cost nothing;
* no drive is ever asked anything on the GUI thread, not even a local one: an external disk that has spun down takes
  seconds to answer. Every drive is asked about on a background thread; a plain local fixed drive shows as ready at once
  (and is corrected if it turns out not to answer), every other drive (network, removable, optical) shows as "checking";
* a drive that is offline, has no media or has not answered yet is not listed at all (it only added clutter); it appears
  by itself if a check finds it ready.

The probes run on daemon threads, so a blocked one never delays closing the app.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Qt, Signal

from . import path_policy

USAGE_CACHE_SECONDS = 30.0
_DEFAULT_NAMES = {
    path_policy.DRIVE_REMOVABLE: "Removable Disk",
    path_policy.DRIVE_FIXED: "Local Disk",
    path_policy.DRIVE_REMOTE: "Network Drive",
    path_policy.DRIVE_CDROM: "CD Drive",
}

READY = "ready"
CHECKING = "checking"
OFFLINE = "offline"

# probe(path) -> (available, volume label, used fraction or None); runs off the GUI thread
Probe = Callable[[str], "tuple[bool, str, float | None]"]


@dataclass
class DriveEntry:
    path: str  # "C:/"
    drive_type: int
    name: str  # the volume label, "" when unknown
    state: str
    ratio: float | None = None
    measured_at: float = 0.0
    generation: int = 0
    probing: bool = False


def storage_numbers(path: str) -> tuple[bool, str, float | None]:
    """(available, volume label, used fraction) for a drive. Blocks (~20 s) on a dead network drive and wakes a
    spun-down disk, so it only ever runs off the GUI thread.

    It is built from ``GetVolumeInformationW`` (through ``ctypes``) and ``shutil.disk_usage`` on purpose: both let go of
    Python's interpreter lock while they wait. ``QStorageInfo`` does not: a probe thread sitting in it for a dead
    ``P:`` froze every Python thread, the main one included, and the app never got past its splash screen.
    """
    root = path.replace("/", "\\") if os.name == "nt" else path
    if os.name == "nt":
        buffer = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(root), buffer, len(buffer), None, None, None, None, 0
        )
        if not ok:
            return False, "", None  # not ready (an empty card reader) or not answering
        label = buffer.value
    else:
        label = ""
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        return False, "", None
    if usage.total <= 0:
        return False, "", None
    return True, label, max(0.0, min(1.0, usage.used / usage.total))


class _ProbeSignals(QObject):
    answered = Signal(str, int, bool, str, float)  # path, generation, available, label, used fraction (-1: unknown)


class DriveListModel(QAbstractListModel):
    """Rows are the drives that are ready. A drive that is offline, has no media (an empty card reader) or is still being
    checked is not listed at all; it appears by itself if a check finds it ready. Every drive's state is kept so it can be
    asked again (``refresh`` / ``sync_roots``)."""

    PathRole = Qt.ItemDataRole.UserRole + 1
    StateRole = Qt.ItemDataRole.UserRole + 2

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        roots_provider: Callable[[], list[tuple[str, int]]] | None = None,
        probe: Probe | None = None,
    ) -> None:
        super().__init__(parent)
        self._entries: list[DriveEntry] = []  # every drive letter, in order
        self._rows: list[DriveEntry] = []  # the ready ones: what the list shows
        self._generation = 0
        self._roots_provider = roots_provider or path_policy.drive_roots
        self._probe: Probe = probe or storage_numbers
        self._signals = _ProbeSignals(self)
        self._signals.answered.connect(self._apply_answer, Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------------ building

    @staticmethod
    def _starting_state(drive_type: int, old: DriveEntry | None) -> str:
        # A plain local drive is listed as ready straight away (and hidden if its check says otherwise). Any other drive
        # stays out of the list until a check finds it ready, except that one that was ready stays while it is re-asked.
        if drive_type == path_policy.DRIVE_FIXED or (old is not None and old.state == READY):
            return READY
        return CHECKING

    def _reset_to(self, entries: list[DriveEntry]) -> None:
        self.beginResetModel()
        self._entries = entries
        self._rows = [entry for entry in entries if entry.state == READY]
        self.endResetModel()

    def refresh(self) -> None:
        """Re-read the drive letters and ask every drive again (in the background)."""
        self._generation += 1
        previous = {entry.path.casefold(): entry for entry in self._entries}
        entries: list[DriveEntry] = []
        for path, drive_type in self._roots_provider():
            old = previous.get(path.casefold())
            entry = DriveEntry(
                path, drive_type, old.name if old else "", self._starting_state(drive_type, old), old.ratio if old else None
            )
            entry.generation = self._generation
            entries.append(entry)
        self._reset_to(entries)
        for entry in entries:
            self._start_probe(entry)

    def sync_roots(self) -> bool:
        """Pick up a drive letter that appeared or went away (a card inserted, a share mapped) without re-asking the
        drives that are still there. Cheap when nothing changed."""
        roots = self._roots_provider()
        if [path.casefold() for path, _kind in roots] == [entry.path.casefold() for entry in self._entries]:
            return False
        existing = {entry.path.casefold(): entry for entry in self._entries}
        entries: list[DriveEntry] = []
        added: list[DriveEntry] = []
        for path, drive_type in roots:
            entry = existing.get(path.casefold())
            if entry is None:
                entry = DriveEntry(path, drive_type, "", self._starting_state(drive_type, None), generation=self._generation)
                added.append(entry)
            entries.append(entry)
        self._reset_to(entries)
        for entry in added:
            self._start_probe(entry)
        return True

    def _start_probe(self, entry: DriveEntry) -> None:
        entry.probing = True
        path, generation, probe, signals = entry.path, entry.generation, self._probe, self._signals

        def run() -> None:
            try:
                available, label, ratio = probe(path)
            except Exception:
                available, label, ratio = False, "", None
            try:
                signals.answered.emit(path, generation, bool(available), label or "", -1.0 if ratio is None else float(ratio))
            except RuntimeError:
                pass  # the model was destroyed while the drive was thinking (the window closed)

        threading.Thread(target=run, name=f"drive-probe-{path}", daemon=True).start()

    def _apply_answer(self, path: str, generation: int, available: bool, label: str, ratio: float) -> None:
        entry = next((e for e in self._entries if e.path.casefold() == path.casefold()), None)
        if entry is None or entry.generation != generation:
            return  # an answer to an older refresh, or for a drive that has gone
        entry.probing = False
        was_listed = entry in self._rows
        entry.state = READY if available else OFFLINE
        if label:
            entry.name = label
        entry.ratio = None if ratio < 0 else ratio
        entry.measured_at = time.monotonic()
        if available and not was_listed:
            position = self._entries.index(entry)
            row = sum(1 for other in self._rows if self._entries.index(other) < position)
            self.beginInsertRows(QModelIndex(), row, row)
            self._rows.insert(row, entry)
            self.endInsertRows()
        elif not available and was_listed:
            row = self._rows.index(entry)
            self.beginRemoveRows(QModelIndex(), row, row)
            self._rows.pop(row)
            self.endRemoveRows()
        elif was_listed:
            row = self._rows.index(entry)
            self.dataChanged.emit(self.index(row), self.index(row))

    # ------------------------------------------------------------------ the file-system-model surface the views use

    def filePath(self, index: QModelIndex) -> str:
        entry = self._entry(index)
        return entry.path if entry is not None else ""

    def is_drive(self, index: QModelIndex) -> bool:
        """Every row of this model is a drive. (Not ``QFileInfo(path).isRoot()``: despite the name it asks the file system,
        so for a dead network drive the answer takes ~20 s and the window freezes while it paints the row.)"""
        return self._entry(index) is not None

    @staticmethod
    def _key(path: str) -> str:
        return path.replace("\\", "/").rstrip("/").casefold()

    def index_for_path(self, path: str) -> QModelIndex:
        """The row of a *listed* drive (a hidden one has no row)."""
        key = self._key(path)
        for row, entry in enumerate(self._rows):
            if self._key(entry.path) == key:
                return self.index(row)
        return QModelIndex()

    def state_of(self, path: str) -> str:
        """``ready`` / ``checking`` / ``offline`` for any drive letter, listed or not ("" for an unknown one)."""
        key = self._key(path)
        entry = next((e for e in self._entries if self._key(e.path) == key), None)
        return entry.state if entry is not None else ""

    def is_available(self, path: str) -> bool:
        return self.state_of(path) == READY

    def usage_ratio(self, path: str) -> float | None:
        entry = self._entry(self.index_for_path(path))
        if entry is None:
            return None
        if not entry.probing and time.monotonic() - entry.measured_at >= USAGE_CACHE_SECONDS:
            self._start_probe(entry)  # the numbers are stale: re-measure in the background, show the old ones meanwhile
        return entry.ratio

    def _entry(self, index: QModelIndex) -> DriveEntry | None:
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        return self._rows[index.row()]

    # ------------------------------------------------------------------ QAbstractListModel

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]
        return 0 if parent.isValid() else len(self._rows)

    def flags(self, index: QModelIndex):  # type: ignore[override]
        if self._entry(index) is None:
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemNeverHasChildren

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        entry = self._entry(index)
        if entry is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            label = entry.name or _DEFAULT_NAMES.get(entry.drive_type, "Drive")
            return f"{label} ({self._letter(entry)})"
        if role == self.PathRole:
            return entry.path
        if role == self.StateRole:
            return entry.state
        return None

    @staticmethod
    def _letter(entry: DriveEntry) -> str:
        return entry.path[:2] if len(entry.path) >= 2 and entry.path[1] == ":" else entry.path
