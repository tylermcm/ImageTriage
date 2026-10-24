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


# The 12 photos of the HTML mockup, used by --mock so the QML shell can be compared with it pixel for pixel.
_MOCK = (
    ("DSC_4201", "NEF", 74, 71, "Balanced", "Low", "1/500 \u00b7 f/8 \u00b7 ISO 100", "35 mm", "07:42", "keep", 3),
    ("DSC_4203", "NEF", 91, 88, "Balanced", "Low", "1/800 \u00b7 f/8 \u00b7 ISO 100", "50 mm", "08:05", "win", 5),
    ("DSC_4207", "NEF", 93, 90, "Balanced", "Low", "1/640 \u00b7 f/9 \u00b7 ISO 100", "24 mm", "08:31", "", 0),
    ("DSC_4210", "JPG", 58, 52, "Dark", "Medium", "1/125 \u00b7 f/4 \u00b7 ISO 400", "70 mm", "09:02", "rej", 1),
    ("DSC_4214", "NEF", 80, 83, "Balanced", "Low", "1/400 \u00b7 f/8 \u00b7 ISO 100", "28 mm", "09:20", "keep", 4),
    ("DSC_4219", "JPG", 41, 38, "Bright", "Low", "1/1000 \u00b7 f/5.6 \u00b7 ISO 100", "35 mm", "09:48", "rej", 2),
    ("DSC_4222", "NEF", 88, 86, "Balanced", "Low", "1/320 \u00b7 f/8 \u00b7 ISO 100", "24 mm", "10:12", "win", 5),
    ("DSC_4226", "NEF", 84, 79, "Balanced", "Low", "1/200 \u00b7 f/6.3 \u00b7 ISO 200", "40 mm", "10:40", "keep", 4),
    ("DSC_4231", "NEF", 69, 64, "Bright", "Medium", "1/160 \u00b7 f/5.6 \u00b7 ISO 800", "70 mm", "11:03", "rev", 3),
    ("DSC_4236", "JPG", 63, 60, "Balanced", "Low", "1/1250 \u00b7 f/8 \u00b7 ISO 100", "24 mm", "11:30", "", 0),
    ("DSC_4240", "NEF", 95, 92, "Balanced", "Low", "1/500 \u00b7 f/8 \u00b7 ISO 100", "35 mm", "12:08", "win", 5),
    ("DSC_4244", "NEF", 86, 82, "Balanced", "Low", "1/400 \u00b7 f/9 \u00b7 ISO 100", "24 mm", "12:36", "keep", 4),
)


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
        self._adv: dict | None = None      # the advanced-filters dialog's part of the query: {"decisions", "min_rating", "file"}
        self._folder = ""
        self._loading = False
        self._revision = 0
        self._loaded.connect(self._apply_loaded)
        self._mock: dict[str, dict] = {}
        self._mock_path = ""

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
                    "companions": tuple(record.companion_paths),
                })
            self._loaded.emit(rows, folder)

        threading.Thread(target=work, name="qml-scan", daemon=True).start()

    def loadMock(self, directory: str) -> None:
        """Open p01..p12.jpg from `directory` with the mockup's exact names, decisions, ratings and capture data."""
        rows = []
        self._mock = {}
        for index, m in enumerate(_MOCK, 1):
            path = os.path.join(directory, f"p{index:02d}.jpg")
            rows.append({"path": path, "name": m[0], "ext": m[1], "raw": m[1] == "NEF", "decision": m[9], "rating": m[10], "ai": m[2]})
            self._mock[m[0]] = {"focus": m[3], "expo": m[4], "noise": m[5], "exposure": m[6], "focal": m[7], "captured": f"2026-05-14 {m[8]}"}
        self._mock_path = "Photos (D:) / 2026 / Georgia trip"
        self._apply_loaded(rows, "Kutaisi, Georgia")

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
            and self._advanced_ok(r, self._adv)
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

    # -- advanced filters (filters_model.py) -------------------------------------------------------------------------------
    @staticmethod
    def _advanced_ok(row: dict, adv: dict | None) -> bool:
        if not adv:
            return True
        if adv["decisions"] is not None and row["decision"] not in adv["decisions"]:
            return False
        if row["rating"] < adv["min_rating"]:
            return False
        return adv["file"] is None or (row["raw"] if adv["file"] == "raw" else not row["raw"])

    def apply_advanced(self, spec: dict | None) -> None:
        self._adv = spec
        self._rebuild()

    def count_matching(self, spec: dict | None) -> int:
        """How many of the folder's photos the dialog's filters keep (the rail's and bar's own filters are not counted)."""
        return sum(1 for r in self._all if self._advanced_ok(r, spec))

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
        self._filter, self._min_rate, self._fmt, self._adv = "all", 0, "all", None
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
        detail = dict(item, isRaw=item["raw"], position=row + 1, count=len(self._rows), exposure=f"{shutter} \u00b7 {aperture} \u00b7 ISO {iso}",
                    focal=("24 mm", "35 mm", "50 mm", "70 mm")[(h >> 14) % 4], captured=f"2026-05-14 {8 + (h >> 17) % 10:02d}:{(h >> 20) % 60:02d}",
                    focus=focus, expo=("Balanced", "Balanced", "Bright", "Dark")[(h >> 22) % 4], noise=("Low", "Low", "Medium")[(h >> 24) % 3],
                    camera="Nikon Z 6", lens="24-70 mm f/4 S", pixels="6048 \u00d7 4024")
        detail.update(self._mock.get(item["name"], {}))
        return detail

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
    leafName = Property(str, lambda self: "Kutaisi" if self._mock else os.path.basename(self._folder.rstrip("/\\")), notify=folderChanged)
    folderPath = Property(str, lambda self: self._mock_path or self._folder, notify=folderChanged)
