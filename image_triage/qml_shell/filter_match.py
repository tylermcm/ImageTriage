"""Applying the Advanced filters query to the open folder's photos with the Widgets app's own rule (`filtering.matches_record_query`).

The shell's photo model keeps the decision and rating part of a query itself (they are what the rail and the floating bar filter on too), and RAW or JPEG for the
sample page's photos. Everything else a query can say is decided here, for the photos read from a folder (a row with its scanner `record`):

- file types (RAW, JPEG, PSD, TIFF, FITS, STL, Edited, Other), the Edited quick filter, and the folder text: from the record;
- AI states, the AI Top Picks and AI Grouped quick filters: from the folder's saved AI report (`ai_scores.py`), so with no report they match nothing, as in the Widgets app;
- camera, lens, orientation, capture dates, ISO and focal length: from each photo's EXIF (`metadata.load_capture_metadata`), read on a worker thread the first
  time a query needs them. Until a photo's turn comes it does not match (the Widgets app's rule), so a big folder fills in as the reading goes.

What it cannot decide is still stored in the query and counted as "not applied": Smart Groups, Duplicates, AI Disagreements, AI Ingested and AI Prefilter Dumped
(they need the review intelligence and the AI workflow), tags (the shell has none), and the search confidence.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from ..filtering import AIStateFilter, RecordFilterQuery, ReviewStateFilter, matches_record_query
from ..models import FilterMode, SessionAnnotation

# the quick filters the photo model decides itself (decision), and the ones nothing in the shell can decide
MODEL_QUICK = (FilterMode.ALL, FilterMode.WINNERS, FilterMode.REJECTS, FilterMode.UNREVIEWED)
UNDECIDABLE_QUICK = (FilterMode.SMART_GROUPS, FilterMode.DUPLICATES, FilterMode.AI_DISAGREEMENTS, FilterMode.AI_INGESTED, FilterMode.AI_PREFILTER_DUMPED)
BATCH = 40                                    # photos read per hand-over to the grid, so the list fills in steps instead of once at the end


def undecidable(query: RecordFilterQuery) -> int:
    """How many set filters of `query` the shell cannot decide (they are kept in the query and not applied)."""
    n = query.quick_filter in UNDECIDABLE_QUICK
    n += query.ai_state == AIStateFilter.DISAGREEMENTS
    n += bool(query.tag_text.strip())
    n += query.min_search_confidence > 0
    return int(n)


def rest_of(query: RecordFilterQuery) -> RecordFilterQuery:
    """`query` without what the photo model decides and what nothing can: the part `FilterMatcher` answers."""
    return replace(
        query,
        quick_filter=query.quick_filter if query.quick_filter not in MODEL_QUICK + UNDECIDABLE_QUICK else FilterMode.ALL,
        review_state=ReviewStateFilter.ALL, min_rating=0, search_text="", person_label="", min_search_confidence=0.0, tag_text="",
        ai_state=query.ai_state if query.ai_state != AIStateFilter.DISAGREEMENTS else AIStateFilter.ALL,
    )


class _ReadSignals(QObject):
    batch = Signal(dict)


class _ReadTask(QRunnable):
    def __init__(self, paths: list[str], signals: _ReadSignals, current) -> None:
        super().__init__()
        self.paths, self.signals, self.current = paths, signals, current
        self.setAutoDelete(True)

    def run(self) -> None:
        from ..metadata import load_capture_metadata

        found: dict = {}
        for path in self.paths:
            if not self.current():
                return                                     # another folder was opened: nobody wants these
            try:
                found[path] = load_capture_metadata(path)
            except Exception:
                found[path] = None
            if len(found) >= BATCH:
                self.signals.batch.emit(found)
                found = {}
        if found:
            self.signals.batch.emit(found)


class FilterMatcher(QObject):
    changed = Signal()                            # more photos' capture data has arrived: the grid decides again

    def __init__(self, ai_result=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ai_result = ai_result or (lambda row: None)        # row -> the saved AI report's result for it (ai_scores.py), or None
        self._meta: dict = {}                                    # path -> CaptureMetadata (None: unreadable)
        self._asked: set[str] = set()
        self._token = 0
        self._signals = _ReadSignals()
        self._signals.batch.connect(self._arrived)
        self._tick = QTimer(self)
        self._tick.setSingleShot(True)
        self._tick.setInterval(150)
        self._tick.timeout.connect(self.changed.emit)

    def forget(self) -> None:
        """A different folder was opened: what was read for the last one is dropped (a photo is read when a query first needs it)."""
        self._token += 1
        self._meta.clear()
        self._asked.clear()

    def need_metadata(self, rows: list[dict]) -> None:
        """Start reading the capture data of the photos whose turn has not come (once each)."""
        todo = [r["path"] for r in rows if r.get("record") is not None and r["path"] not in self._asked]
        if not todo:
            return
        self._asked.update(todo)
        token = self._token
        QThreadPool.globalInstance().start(_ReadTask(todo, self._signals, lambda: token == self._token))

    def _arrived(self, found: dict) -> None:
        self._meta.update(found)
        self._tick.start()                                       # one redraw for a burst of batches

    def loaded(self) -> tuple[int, int]:
        """(photos read, photos asked for) of the capture data, for a progress line."""
        return len(self._meta), len(self._asked)

    def matches(self, row: dict, query: RecordFilterQuery) -> bool:
        """Does this photo satisfy the part of `query` that is not the model's own? `query` is `rest_of(...)`."""
        record = row.get("record")
        if record is None:
            return True
        annotation = SessionAnnotation(winner=row.get("decision") == "win", reject=row.get("decision") == "rej", rating=int(row.get("rating") or 0))
        metadata = self._meta.get(row["path"]) if query.requires_metadata else None
        needs_ai = query.ai_state != AIStateFilter.ALL or query.quick_filter in (FilterMode.AI_TOP_PICKS, FilterMode.AI_GROUPED)
        ai_result = self._ai_result(row) if needs_ai else None
        return matches_record_query(record, query, annotation=annotation, ai_result=ai_result, metadata=metadata)
