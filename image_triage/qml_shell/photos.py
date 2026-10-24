"""The photo list the QML grid shows: real files from the existing scanner, with decision, rating and sort/filter state.

Decisions, ratings and AI scores are sample data for now (derived from the file name so they are stable between runs); the shape
(roles, filtering, sorting, counts) is what the real stores will plug into.
"""
from __future__ import annotations

import os
import threading
import zlib
from pathlib import Path

from PySide6.QtCore import Property, QAbstractListModel, QByteArray, QModelIndex, Qt, Signal, Slot

from ..formats import RAW_SUFFIXES
from ..scanner import scan_folder

PathRole, NameRole, ExtRole, RawRole, DecisionRole, RatingRole, AiRole = (Qt.ItemDataRole.UserRole + n for n in range(1, 8))

_DECISIONS = ("", "keep", "win", "rej", "rev", "", "keep", "")  # weights by position


def _sample(name: str) -> tuple[str, int, int]:
    h = zlib.crc32(name.encode("utf-8", "replace"))
    return _DECISIONS[h % len(_DECISIONS)], (h >> 3) % 6, 40 + (h >> 7) % 60


class PhotoModel(QAbstractListModel):
    countsChanged = Signal()
    revisionChanged = Signal()
    folderChanged = Signal()
    loadingChanged = Signal()
    _loaded = Signal(list, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all: list[dict] = []
        self._rows: list[dict] = []
        self._filter = "all"
        self._min_rate = 0
        self._fmt = "all"
        self._sort = "name"
        self._folder = ""
        self._loading = False
        self._revision = 0
        self._loaded.connect(self._apply_loaded)

    # -- model ------------------------------------------------------------------------------------------------------------
    def roleNames(self) -> dict:
        return {
            PathRole: QByteArray(b"path"), NameRole: QByteArray(b"name"), ExtRole: QByteArray(b"ext"), RawRole: QByteArray(b"isRaw"),
            DecisionRole: QByteArray(b"decision"), RatingRole: QByteArray(b"rating"), AiRole: QByteArray(b"ai"),
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == PathRole:
            return row["path"]
        if role == NameRole:
            return row["name"]
        if role == ExtRole:
            return row["ext"]
        if role == RawRole:
            return row["raw"]
        if role == DecisionRole:
            return row["decision"]
        if role == RatingRole:
            return row["rating"]
        if role == AiRole:
            return row["ai"]
        return None

    # -- loading -----------------------------------------------------------------------------------------------------------
    @Slot(str)
    def loadFolder(self, folder: str) -> None:
        if self._loading:
            return
        self._loading = True
        self.loadingChanged.emit()

        def work() -> None:
            try:
                records = [r for r in scan_folder(folder) if not r.is_folder]
            except Exception:
                records = []
            rows = []
            for record in records:
                suffix = Path(record.path).suffix.lower()
                decision, rating, ai = _sample(record.name)
                rows.append({
                    "path": record.path, "name": record.name, "ext": suffix.lstrip(".").upper() or "FILE",
                    "raw": suffix in RAW_SUFFIXES, "decision": decision, "rating": rating, "ai": ai,
                })
            self._loaded.emit(rows, folder)

        threading.Thread(target=work, name="qml-scan", daemon=True).start()

    def _apply_loaded(self, rows: list, folder: str) -> None:
        self._all = rows
        self._folder = folder
        self._loading = False
        self._rebuild()
        self.loadingChanged.emit()
        self.folderChanged.emit()

    def setRows(self, rows: list[dict], folder: str = "") -> None:
        """Replace everything at once (benchmarks, tests)."""
        self._apply_loaded(rows, folder)

    def _rebuild(self) -> None:
        rows = [
            r for r in self._all
            if (self._filter == "all" or r["decision"] == self._filter)
            and r["rating"] >= self._min_rate
            and (self._fmt == "all" or (r["raw"] if self._fmt == "raw" else not r["raw"]))
        ]
        if self._sort == "name":
            rows.sort(key=lambda r: r["name"].casefold())
        elif self._sort == "ai":
            rows.sort(key=lambda r: -r["ai"])
        elif self._sort == "rating":
            rows.sort(key=lambda r: -r["rating"])
        elif self._sort == "decision":
            order = {"win": 0, "keep": 1, "rev": 2, "": 3, "rej": 4}
            rows.sort(key=lambda r: order.get(r["decision"], 3))
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()
        self.countsChanged.emit()
        self._bump()

    # -- view state --------------------------------------------------------------------------------------------------------
    @Slot(str)
    def setFilter(self, decision: str) -> None:
        if decision != self._filter:
            self._filter = decision
            self._rebuild()

    @Slot(int)
    def setMinRating(self, value: int) -> None:
        if value != self._min_rate:
            self._min_rate = value
            self._rebuild()

    @Slot(str)
    def setFormat(self, fmt: str) -> None:
        if fmt != self._fmt:
            self._fmt = fmt
            self._rebuild()

    @Slot(str)
    def setSort(self, key: str) -> None:
        if key != self._sort:
            self._sort = key
            self._rebuild()

    @Slot()
    def clearFilters(self) -> None:
        self._filter, self._min_rate, self._fmt = "all", 0, "all"
        self._rebuild()

    @Slot(int, str)
    def setDecision(self, row: int, decision: str) -> None:
        if not 0 <= row < len(self._rows):
            return
        target = self._rows[row]
        target["decision"] = "" if target["decision"] == decision else decision
        index = self.index(row)
        self.dataChanged.emit(index, index, [DecisionRole])
        self.countsChanged.emit()
        self._bump()

    @Slot(int, int)
    def setRating(self, row: int, value: int) -> None:
        if not 0 <= row < len(self._rows):
            return
        target = self._rows[row]
        target["rating"] = 0 if target["rating"] == value else value
        index = self.index(row)
        self.dataChanged.emit(index, index, [RatingRole])
        self._bump()

    def _bump(self) -> None:
        self._revision += 1
        self.revisionChanged.emit()

    @Slot(int, result="QVariantMap")
    def get(self, row: int) -> dict:
        """Everything the inspector shows for one row (sample capture data, derived from the name so it is stable)."""
        if not 0 <= row < len(self._rows):
            return {}
        item = self._rows[row]
        h = zlib.crc32(item["name"].encode("utf-8", "replace"))
        shutter = ("1/125", "1/250", "1/500", "1/800", "1/1250")[h % 5]
        aperture = ("f/4", "f/5.6", "f/8", "f/9", "f/11")[(h >> 4) % 5]
        iso = (100, 100, 200, 400, 800)[(h >> 8) % 5]
        focus = 45 + (h >> 11) % 55
        return dict(item, isRaw=item["raw"], position=row + 1, count=len(self._rows), exposure=f"{shutter} \u00b7 {aperture} \u00b7 ISO {iso}",
                    focal=("24 mm", "35 mm", "50 mm", "70 mm")[(h >> 14) % 4], captured=f"2026-05-14 {8 + (h >> 17) % 10:02d}:{(h >> 20) % 60:02d}",
                    focus=focus, expo=("Balanced", "Balanced", "Bright", "Dark")[(h >> 22) % 4], noise=("Low", "Low", "Medium")[(h >> 24) % 3],
                    camera="Nikon Z 6", lens="24-70 mm f/4 S", pixels="6048 \u00d7 4024")

    # -- properties for the bar and header ---------------------------------------------------------------------------------
    def _count(self, decision: str) -> int:
        return sum(1 for r in self._all if r["decision"] == decision)

    revision = Property(int, lambda self: self._revision, notify=revisionChanged)
    total = Property(int, lambda self: len(self._all), notify=countsChanged)
    shown = Property(int, lambda self: len(self._rows), notify=countsChanged)
    winCount = Property(int, lambda self: self._count("win"), notify=countsChanged)
    keepCount = Property(int, lambda self: self._count("keep"), notify=countsChanged)
    rejCount = Property(int, lambda self: self._count("rej"), notify=countsChanged)
    loading = Property(bool, lambda self: self._loading, notify=loadingChanged)
    folderName = Property(str, lambda self: os.path.basename(self._folder.rstrip("/\\")) or self._folder, notify=folderChanged)
    folderPath = Property(str, lambda self: self._folder, notify=folderChanged)
