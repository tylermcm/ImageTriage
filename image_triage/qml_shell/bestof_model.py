"""Best-of-Set Auto Assembly for the QML shell: the Widgets app's own planner (`workflows.best_of.build_best_of_set_plan`).

The planner ranks by AI results, burst groups and duplicate analysis. The shell has none of those yet (its AI scores are sample data and
its decisions are the only real signal), so it is given the user's winners as annotations: winners rank first, the rest by name. The
Widgets app applies the shortlist as a selection; the shell has one selected cell and no multi-select, so the shortlist **becomes the view**
(the grid shows only the proposed photos, with the Add All bar offering them to a collection) until "Back to folder".
"""
from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..models import ImageRecord, SessionAnnotation
from ..scanner import normalized_path_key
from ..workflows import BEST_OF_BALANCED, BEST_OF_TOP_N, BEST_OF_TOP_PER_GROUP, build_best_of_set_plan

STRATEGIES = (
    (BEST_OF_BALANCED, "Balanced Shortlist", "The best frame of each group first, then the strongest of the rest."),
    (BEST_OF_TOP_N, "Top N Overall", "The highest-scoring photos, whichever group they are in."),
    (BEST_OF_TOP_PER_GROUP, "Top Per Group", "One pick from every burst or AI group, strongest first."),
)


class BestOfModel(QObject):
    changed = Signal()

    def __init__(self, photos, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._strategy = BEST_OF_BALANCED
        self._size = 6
        self._summary = ""
        photos.countsChanged.connect(self.changed)

    @Slot(result="QVariantList")
    def strategies(self) -> list:
        return [{"value": k, "label": label, "desc": desc} for k, label, desc in STRATEGIES]

    total = Property(int, lambda self: self._photos.shown, notify=changed)

    @Slot(result=int)
    def defaultSize(self) -> int:
        """The Widgets dialog's starting size: 6 to 12 of what is in view."""
        n = self._photos.shown
        return max(1, min(max(6, min(n, 12)), n)) if n else 1

    @Slot(str, int)
    def build(self, strategy: str, size: int) -> None:
        p = self._photos
        rows = [p.get(i) for i in range(p.shown)]
        rows = [r for r in rows if r]
        records = [ImageRecord(path=r["path"], name=r["name"], size=0, modified_ns=0, companion_paths=tuple(r.get("companions", ()))) for r in rows]
        annotations = {r["path"]: SessionAnnotation(winner=True) for r in rows if r["decision"] == "win"}
        plan = build_best_of_set_plan(records, ai_bundle=None, review_bundle=None, burst_recommendations={}, annotations_by_path=annotations,
                                      limit=max(1, size), strategy=strategy or BEST_OF_BALANCED)
        keys = {normalized_path_key(c.path) for c in plan.candidates}
        self._summary = " ".join(plan.summary_lines)
        p.set_scope("best", strategy, lambda r, k=keys: normalized_path_key(r["path"]) in k)
        self.changed.emit()

    summary = Property(str, lambda self: self._summary, notify=changed)
