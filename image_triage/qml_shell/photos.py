"""The photo list the QML grid shows: real files from the existing scanner, with decision, rating and sort/filter state.

A real folder's decisions and ratings come from the Widgets decision store through `set_annotation_loader` (decision_bridge.py), and every change is
announced on `annotationChanged` so the bridge can save it; `undo` steps back through the changes made in this folder. A photo's AI score is -1 until
the folder's saved AI report has been read (`ai_scores.py` calls `rescore`).
"""
from __future__ import annotations

import os
import threading
import zlib
from pathlib import Path

from PySide6.QtCore import Property, QAbstractListModel, QByteArray, QModelIndex, Qt, Signal, Slot

from ..formats import RAW_SUFFIXES
from ..scanner import scan_folder
from .inspector_sample import sample_ai, sample_measured

PathRole, NameRole, ExtRole, RawRole, DecisionRole, RatingRole, AiRole, TitleRole, SelectedRole = (Qt.ItemDataRole.UserRole + n for n in range(1, 10))

_DECISIONS = ("", "keep", "win", "rej", "rev", "", "keep", "")  # weights by position


def _title(row: dict) -> str:
    """The file name without its extension: the grid shows the format as a tag, so the name does not repeat it (R.bmp -> R)."""
    name, ext = row["name"], "." + str(row.get("ext", "")).lower()
    stem = name[: -len(ext)] if len(ext) > 1 and name.lower().endswith(ext) else name
    return stem or name                  # a file that is nothing but its extension keeps its name


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


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
    scopeChanged = Signal()
    loadingChanged = Signal()
    annotationChanged = Signal(object)      # a row's decision or rating changed (the row dict, with its scanner record): save it
    undoChanged = Signal()
    selectionChanged = Signal()             # the set of photos picked with Ctrl, Shift or Ctrl+A changed
    _loaded = Signal(list, str, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all: list[dict] = []
        self._annotation_loader = None      # fn(records) -> {path: {"decision", "rating", "annotation"}}, called off the UI thread
        self._next_undo_id = 0
        self._history: list[dict] = []      # what undo steps back through: {"path", "decision": (before, after), "rating": (before, after)}
        self._rows: list[dict] = []
        self._selected: set[str] = set()    # photos picked on purpose (by `_path_key`): empty means "just the one the grid is on", and a scope of every photo shown
        self._filter = "all"
        self._min_rate = 0
        self._fmt = "all"
        self._sort = "name"
        self._adv: dict | None = None      # the advanced-filters dialog's part of the query: {"decisions", "min_rating", "file"}
        self._folder = ""
        self._loading = False
        self._load_token = 0                # the newest loadFolder request: an older scan that finishes later is dropped
        self._revision = 0
        self._loaded.connect(self._apply_loaded)
        self._mock: dict[str, dict] = {}
        self._mock_path = ""
        self._scope_kind = ""           # "" (the folder), "collection", "winners", "person" or "best": what the grid is limited to
        self._scope_id = ""
        self._scope_pred = None
        self._row_of_path: dict[str, int] | None = None     # path -> shown row; made when first asked for, dropped on every rebuild
        self._row_of_key: dict[str, int] | None = None      # the same by normalised path (case and slashes), for paths another program wrote

    # -- model ------------------------------------------------------------------------------------------------------------
    def roleNames(self) -> dict:
        return {
            PathRole: QByteArray(b"path"), NameRole: QByteArray(b"name"), ExtRole: QByteArray(b"ext"), RawRole: QByteArray(b"isRaw"),
            DecisionRole: QByteArray(b"decision"), RatingRole: QByteArray(b"rating"), AiRole: QByteArray(b"ai"), TitleRole: QByteArray(b"title"), SelectedRole: QByteArray(b"selected"),
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
        if role == TitleRole:
            return _title(row)
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
        if role == SelectedRole:
            return _path_key(row["path"]) in self._selected
        return None

    # -- loading -----------------------------------------------------------------------------------------------------------
    @Slot(str)
    def loadFolder(self, folder: str) -> None:
        self._load_token += 1               # a click on another folder while one is still being read wins
        token = self._load_token
        if not self._loading:
            self._loading = True
            self.loadingChanged.emit()

        def work() -> None:
            try:
                records = [r for r in scan_folder(folder) if not r.is_folder]
            except Exception:
                records = []
            saved: dict = {}
            if self._annotation_loader is not None and records:
                try:
                    saved = self._annotation_loader(records)
                except Exception:                      # an unreadable store: the folder opens with nothing decided
                    saved = {}
            rows = []
            for record in records:
                suffix = Path(record.path).suffix.lower()
                # What the decision store holds for the photo (nothing is rated or decided until the user does it), and no AI score (-1)
                # until the folder's saved AI report is read
                mine = saved.get(record.path) or {}
                decision, rating, ai = mine.get("decision", ""), mine.get("rating", 0), -1
                rows.append({
                    "path": record.path, "name": record.name, "ext": suffix.lstrip(".").upper() or "FILE",
                    "raw": suffix in RAW_SUFFIXES, "decision": decision, "rating": rating, "ai": ai,
                    "companions": tuple(record.companion_paths), "record": record, "annotation": mine.get("annotation"),
                })
            self._loaded.emit(rows, folder, token)

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

    def _apply_loaded(self, rows: list, folder: str, token: int = -1) -> None:
        if token != -1 and token != self._load_token:
            return                          # superseded by a newer loadFolder: that one reports when it is done
        self._all = rows
        if folder != self._folder and self._selected:
            self._selected = set()
            self.selectionChanged.emit()
        if folder != self._folder:
            self._scope_kind, self._scope_id, self._scope_pred = "", "", None      # a collection or person is not a folder: opening one leaves it
        if folder != self._folder:
            self._history = []
            self.undoChanged.emit()
        self._folder = folder
        self._loading = False
        self._rebuild()
        self.loadingChanged.emit()
        self.folderChanged.emit()

    def setRows(self, rows: list[dict], folder: str = "") -> None:
        """Replace everything at once (benchmarks, tests)."""
        self._apply_loaded(rows, folder)

    def _scoped(self) -> list[dict]:
        pred = self._scope_pred
        return self._all if pred is None else [r for r in self._all if pred(r)]

    def set_scope(self, kind: str = "", ident: str = "", predicate=None) -> None:
        """Limit the grid to the folder's photos that satisfy `predicate(row)` (a collection, the winners, a person, a shortlist).

        Collections and people can span folders; the shell only has the open folder's photos, so only those are shown."""
        self._scope_kind, self._scope_id, self._scope_pred = kind, ident, predicate
        self._rebuild()
        self.scopeChanged.emit()

    def _rebuild(self) -> None:
        rows = [
            r for r in self._scoped()
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
        self._row_of_path = self._row_of_key = None
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

    def collect(self, *, row: int | None = None, decision: str | None = None, min_rating: int = 0) -> list[dict]:
        """Photos for sharing: the shown row `row`, or everything in the folder with `decision` and at least `min_rating` stars.

        Each is {"path", "name", "companions"}; a RAW's JPEG (and the like) travels with it."""
        if row is not None:
            source = [self._rows[row]] if 0 <= row < len(self._rows) else []
        else:
            source = [r for r in self._all if (decision is None or r["decision"] == decision) and r["rating"] >= min_rating]
        return [{"path": r["path"], "name": r["name"], "decision": r["decision"], "companions": tuple(r.get("companions", ()))} for r in source]

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

    def set_annotation_loader(self, loader) -> None:
        """`loader(records)` returns {path: {"decision", "rating", "annotation"}} for the photos the decision store knows; it runs on the scan thread."""
        self._annotation_loader = loader

    def _record_change(self, target: dict, decision: tuple, rating: tuple) -> None:
        if decision[0] == decision[1] and rating[0] == rating[1]:
            return
        self._history.append({"path": target["path"], "decision": decision, "rating": rating})
        del self._history[:-200]
        self.undoChanged.emit()
        if target.get("record") is not None:
            self.annotationChanged.emit(target)

    # -- picking several photos ---------------------------------------------------------------------------------------------
    def _notify_selection(self, rows: list[int]) -> None:
        for row in rows:
            index = self.index(row)
            self.dataChanged.emit(index, index, [SelectedRole])
        self.selectionChanged.emit()

    def _selected_rows(self) -> list[int]:
        return [i for i, r in enumerate(self._rows) if _path_key(r["path"]) in self._selected]

    @Slot()
    def clearSelection(self) -> None:
        if not self._selected:
            return
        before = self._selected_rows()
        self._selected = set()
        self._notify_selection(before)

    @Slot(int, int)
    def toggleSelected(self, row: int, current: int = -1) -> None:
        """Ctrl+click: add the photo to the pick or take it out. With nothing picked yet the photo the grid was on (`current`) is picked first."""
        if not 0 <= row < len(self._rows):
            return
        changed = []
        if not self._selected and 0 <= current < len(self._rows) and current != row:
            self._selected.add(_path_key(self._rows[current]["path"]))
            changed.append(current)
        key = _path_key(self._rows[row]["path"])
        self._selected.symmetric_difference_update({key})
        changed.append(row)
        self._notify_selection(changed)

    @Slot(int, int)
    def selectRange(self, anchor: int, row: int) -> None:
        """Shift+click or Shift+arrow: pick every photo from `anchor` to `row`."""
        if not self._rows:
            return
        low, high = sorted((max(0, min(len(self._rows) - 1, anchor)), max(0, min(len(self._rows) - 1, row))))
        before = set(self._selected_rows())
        self._selected = {_path_key(self._rows[i]["path"]) for i in range(low, high + 1)}
        self._notify_selection(sorted(before | set(range(low, high + 1))))

    @Slot()
    def selectAll(self) -> None:
        if not self._rows:
            return
        self._selected = {_path_key(r["path"]) for r in self._rows}
        self._notify_selection(list(range(len(self._rows))))

    @Slot(int, result="QVariantList")
    def selectionRows(self, current: int) -> list:
        """The rows an action applies to: the ones picked on purpose, else the row the grid is on."""
        picked = self._selected_rows()
        return picked if picked else ([current] if 0 <= current < len(self._rows) else [])

    def scope_rows(self) -> list[dict]:
        """The photos a batch or a handoff takes: the ones picked on purpose, else every photo shown."""
        picked = self._selected_rows()
        return [self._rows[i] for i in picked] if picked else list(self._rows)

    picked = Property(int, lambda self: len(self._selected_rows()), notify=selectionChanged)

    def _decide_many(self, rows: list[int], decision: str | None = None, rating: int | None = None) -> None:
        """Set a decision or a rating on several photos as one step (Ctrl+Z takes all of them back). It clears when every one already has it."""
        targets = [self._rows[i] for i in rows if 0 <= i < len(self._rows)]
        if not targets:
            return
        field, value = ("decision", decision) if decision is not None else ("rating", rating)
        new = "" if field == "decision" else 0
        if not all(t[field] == value for t in targets):
            new = value
        before = {t["path"]: (t["decision"], t["rating"]) for t in targets}
        for t in targets:
            t[field] = new
        for i in rows:
            if 0 <= i < len(self._rows):
                index = self.index(i)
                self.dataChanged.emit(index, index, [DecisionRole if field == "decision" else RatingRole])
        self.countsChanged.emit()
        self._bump()
        changed = [t for t in targets if before[t["path"]] != (t["decision"], t["rating"])]
        for t in changed:
            if t.get("record") is not None:
                self.annotationChanged.emit(t)
        if not changed:
            return

        def undo() -> bool:
            for t in changed:
                mine = next((r for r in self._all if r["path"] == t["path"]), None)
                if mine is not None:
                    mine["decision"], mine["rating"] = before[t["path"]]
                    if mine.get("record") is not None:
                        self.annotationChanged.emit(mine)
            self._rebuild()
            return True

        self.push_undo(f"{field} of {len(changed)} photos", undo)

    @Slot("QVariantList", str)
    def setDecisionMany(self, rows: list, decision: str) -> None:
        if len(rows) == 1:
            self.setDecision(int(rows[0]), decision)
        else:
            self._decide_many([int(r) for r in rows], decision=decision)

    @Slot("QVariantList", int)
    def setRatingMany(self, rows: list, value: int) -> None:
        if len(rows) == 1:
            self.setRating(int(rows[0]), value)
        else:
            self._decide_many([int(r) for r in rows], rating=value)

    @Slot(int, str)
    def setDecision(self, row: int, decision: str) -> None:
        if not 0 <= row < len(self._rows):
            return
        target = self._rows[row]
        before = target["decision"]
        target["decision"] = "" if before == decision else decision
        index = self.index(row)
        self.dataChanged.emit(index, index, [DecisionRole])
        self.countsChanged.emit()
        self._bump()
        self._record_change(target, (before, target["decision"]), (target["rating"],) * 2)

    @Slot(int, int)
    def setRating(self, row: int, value: int) -> None:
        if not 0 <= row < len(self._rows):
            return
        target = self._rows[row]
        before = target["rating"]
        target["rating"] = 0 if before == value else value
        index = self.index(row)
        self.dataChanged.emit(index, index, [RatingRole])
        self._bump()
        self._record_change(target, (target["decision"],) * 2, (before, target["rating"]))

    canUndo = Property(bool, lambda self: bool(self._history), notify=undoChanged)

    def push_undo(self, label: str, undo_fn) -> int:
        """Add a step that is not a decision or a rating (a delete, a move): `undo_fn()` takes it back and returns True when it did. Returns the step's id."""
        self._next_undo_id += 1
        self._history.append({"id": self._next_undo_id, "label": label, "call": undo_fn})
        del self._history[:-200]
        self.undoChanged.emit()
        return self._next_undo_id

    def undo_entry(self, entry_id: int) -> bool:
        """Take back one specific step (a toast's Undo button), wherever it is in the history."""
        for i, step in enumerate(self._history):
            if step.get("id") == entry_id:
                del self._history[i]
                self.undoChanged.emit()
                return bool(step["call"]())
        return False

    @Slot(result=str)
    def undo(self) -> str:
        """Step back the last change made in this folder (a decision, a rating, a delete or a move); returns what it undid ("" when there is nothing)."""
        while self._history:
            step = self._history.pop()
            self.undoChanged.emit()
            if "call" in step:
                try:
                    done = bool(step["call"]())
                except OSError:
                    done = False
                if done:
                    return step["label"]
                continue
            target = next((r for r in self._all if r["path"] == step["path"]), None)
            if target is None:
                continue                           # the photo is gone (moved or deleted): its change cannot be taken back
            target["decision"], target["rating"] = step["decision"][0], step["rating"][0]
            self._rebuild()
            self.countsChanged.emit()
            self._bump()
            if target.get("record") is not None:
                self.annotationChanged.emit(target)
            what = "decision" if step["decision"][0] != step["decision"][1] else "rating"
            return f"{what} of {target['name']}"
        return ""

    def remove_paths(self, paths) -> None:
        """Take photos out of the list (they were deleted or moved away) without reading the folder again."""
        gone = {_path_key(p) for p in paths}
        self._all = [r for r in self._all if _path_key(r["path"]) not in gone]
        self._history = [h for h in self._history if "call" in h or _path_key(h["path"]) not in gone]
        self._row_of_path = self._row_of_key = None
        self._rebuild()
        self.countsChanged.emit()
        self._bump()
        self.undoChanged.emit()

    @Slot()
    def reload(self) -> None:
        """Read the open folder again (after files came back or went away)."""
        if self._folder and not self._mock:
            self.loadFolder(self._folder)

    def _bump(self) -> None:
        self._revision += 1
        self.revisionChanged.emit()

    @Slot(int, result="QVariantMap")
    def get(self, row: int) -> dict:
        """Everything the inspector shows for one row: the row, its position, and sample capture data derived from the name so it is stable.
        The Quality and AI tabs are not here (`inspection` and `aiScores` answer those) except for the mockup's twelve photos."""
        if not 0 <= row < len(self._rows):
            return {}
        item = self._rows[row]
        h = zlib.crc32(item["name"].encode("utf-8", "replace"))
        shutter = ("1/125", "1/250", "1/500", "1/800", "1/1250")[h % 5]
        aperture = ("f/4", "f/5.6", "f/8", "f/9", "f/11")[(h >> 4) % 5]
        iso = (100, 100, 200, 400, 800)[(h >> 8) % 5]
        detail = dict(item, isRaw=item["raw"], position=row + 1, count=len(self._rows), exposure=f"{shutter} \u00b7 {aperture} \u00b7 ISO {iso}",
                    focal=("24 mm", "35 mm", "50 mm", "70 mm")[(h >> 14) % 4], captured=f"2026-05-14 {8 + (h >> 17) % 10:02d}:{(h >> 20) % 60:02d}",
                    camera="Nikon Z 6", lens="24-70 mm f/4 S", pixels="6048 \u00d7 4024")
        mock = self._mock.get(item["name"])
        if mock:
            group = [{"path": self._rows[(row + n) % len(self._rows)]["path"], "row": (row + n) % len(self._rows), "current": n == 0} for n in range(4)]
            detail.update(mock, measured=sample_measured(mock["focus"], mock["expo"], mock["noise"]),
                          aiCall=sample_ai(item["ai"], item["decision"], mock["focus"], mock["expo"], group))
        detail.pop("record", None)           # Python objects: QML has no use for them
        detail.pop("annotation", None)
        return detail

    def all_rows(self) -> list[dict]:
        """Every photo of the open folder, shown or not (the list itself: do not change it)."""
        return self._all

    def folder_dir(self) -> str:
        """The folder on the disk the shown photos were read from ("" for the mockup's sample page)."""
        return "" if self._mock else self._folder

    def rescore(self, score_of) -> None:
        """Give every photo its AI score: `score_of(row)` is 0..100, or None for a photo the AI has not scored (it gets -1)."""
        for row in self._all:
            value = score_of(row)
            row["ai"] = -1 if value is None else int(value)
        if self._sort == "ai":
            self._rebuild()
        elif self._rows:
            self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1), [AiRole])
            self._bump()

    def row_at(self, row: int) -> dict | None:
        """The shown row `row` as the model holds it (path, name, decision, rating, and the scanner's `record` for real folders)."""
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_for_path(self, path: str) -> int:
        """Where `path` is among the shown photos, or -1 (a path written with other slashes or capitals still finds its photo)."""
        if self._row_of_path is None:
            self._row_of_path = {r["path"]: i for i, r in enumerate(self._rows)}
        row = self._row_of_path.get(path)
        if row is not None:
            return row
        if self._row_of_key is None:
            self._row_of_key = {_path_key(r["path"]): i for i, r in enumerate(self._rows)}
        return self._row_of_key.get(_path_key(path), -1)

    # -- properties for the bar and header ---------------------------------------------------------------------------------
    def _count(self, decision: str) -> int:
        return sum(1 for r in self._scoped() if r["decision"] == decision)

    revision = Property(int, lambda self: self._revision, notify=revisionChanged)
    total = Property(int, lambda self: len(self._scoped()), notify=countsChanged)
    scopeKind = Property(str, lambda self: self._scope_kind, notify=scopeChanged)
    scopeId = Property(str, lambda self: self._scope_id, notify=scopeChanged)
    shown = Property(int, lambda self: len(self._rows), notify=countsChanged)
    winCount = Property(int, lambda self: self._count("win"), notify=countsChanged)
    keepCount = Property(int, lambda self: self._count("keep"), notify=countsChanged)
    rejCount = Property(int, lambda self: self._count("rej"), notify=countsChanged)
    loading = Property(bool, lambda self: self._loading, notify=loadingChanged)
    folderName = Property(str, lambda self: os.path.basename(self._folder.rstrip("/\\")) or self._folder, notify=folderChanged)
    leafName = Property(str, lambda self: "Kutaisi" if self._mock else os.path.basename(self._folder.rstrip("/\\")), notify=folderChanged)
    folderPath = Property(str, lambda self: self._mock_path or self._folder, notify=folderChanged)
