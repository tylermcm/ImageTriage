"""Best-of-Set Auto Assembly for the QML shell: the shared planner (`workflows.best_of.build_best_of_set_plan`).

The planner ranks by AI results, burst groups and duplicate analysis. The shell has none of those yet (its AI scores are sample data and
its decisions are the only real signal), so it is given the user's winners as annotations: winners rank first, the rest by name. The
Widgets app applies the shortlist as a selection; the shell has one selected cell and no multi-select, so the shortlist **becomes the view**
(the grid shows only the proposed photos, with the Add All bar offering them to a collection) until "Back to folder".

**From AI results.** When the open folder has a saved AI report, the dialog can also choose from it without analysing anything again
(`ai_best_of`): the top photos overall, per stack, or per person, ranked by a score the report really holds. The report and the
folder's people are read on a worker thread when the dialog opens (`prepare`); `aiInfo` says what the report offers, and says plainly
when there is no report or it lacks the score a strategy needs. The picks become the view, as above, and can be marked as Winners or
added to a collection in the same step: either is one entry in the undo history.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Property, QObject, Signal, Slot

from .. import ai_best_of

from ..models import ImageRecord, SessionAnnotation
from ..scanner import normalized_path_key
from ..workflows import BEST_OF_BALANCED, BEST_OF_TOP_N, BEST_OF_TOP_PER_GROUP, build_best_of_set_plan

STRATEGIES = (
    (BEST_OF_BALANCED, "Best mix", "Start with one photo per stack, then add extras to reach your total."),
    (BEST_OF_TOP_N, "Best overall", "Take the highest-ranked photos, even if some look alike."),
    (BEST_OF_TOP_PER_GROUP, "One per stack", "Keep just one photo from each stack of similar shots."),
)


def load_report(folder: str, rows: list[dict]) -> tuple[bool, list, dict | None]:
    """(a saved report exists, its candidates for `rows`, the folder's people or None). Slow: only called from a worker thread."""
    from .ai_scores import load_bundle_shared
    from .photos import member_key

    bundle = load_bundle_shared(folder)
    if bundle is None:
        return False, [], None
    candidates = ai_best_of.candidates_from_bundle(bundle, rows)
    by_key = {member_key(c.path): c.path for c in candidates}
    people: dict[str, list[str]] = {}
    try:
        from ..face_groups import face_group_photo_paths
        from .people_model import PeopleModel, _read_people

        db = PeopleModel._db_path(folder)
        for person in (_read_people(db) if db is not None else []):
            paths = [by_key[key] for key in {member_key(p) for p in face_group_photo_paths(db, person["clusters"])} if key in by_key]
            if paths:
                people[person["name"] or f"Unnamed person {person['id']}"] = paths
    except Exception:                               # the people index is extra: without it the other two ways of choosing still work
        people = {}
    if not people and getattr(bundle, "native_document", None) is not None:
        # The native engine numbers the people of a run; they are not named until the app's own index has read them.
        for path, item in bundle.native_document.get("files", {}).items():
            for number in item.get("people") or ():
                if member_key(path) in by_key:
                    people.setdefault(f"Person {number}", []).append(by_key[member_key(path)])
    return True, candidates, people or None


class BestOfModel(QObject):
    changed = Signal()
    _loaded = Signal(int, object)

    def __init__(self, photos, parent: QObject | None = None, *, collections=None, toasts=None, loader=None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._collections = collections               # None: the picks cannot be added to a collection from here
        self._toasts = toasts
        self._loader = loader or load_report
        self._strategy = BEST_OF_BALANCED
        self._size = 6
        self._summary = ""
        self._token = 0
        self._ai_state = "none"                       # "loading", "none" (no saved report) or "ready"
        self._candidates: list = []
        self._people: dict | None = None
        self._loaded.connect(self._arrived)
        photos.countsChanged.connect(self.changed)
        photos.folderChanged.connect(self._forget)

    # -- choosing from the saved AI report --------------------------------------------------------------------------------------------
    def _forget(self) -> None:
        self._token += 1
        self._ai_state, self._candidates, self._people = "none", [], None
        self.changed.emit()

    def _shown_rows(self) -> list[dict]:
        p = self._photos
        return [row for row in (p.get(i) for i in range(p.shown)) if row]

    @Slot()
    def prepare(self) -> None:
        """Read the open folder's saved AI report and its people for the photos shown, off the UI thread. Called when the dialog opens."""
        folder = self._photos.folder_dir()
        self._token += 1
        token = self._token
        if not folder:
            self._ai_state, self._candidates, self._people = "none", [], None
            self.changed.emit()
            return
        self._ai_state = "loading"
        self.changed.emit()
        rows = [{"path": r["path"], "record": r.get("record"), "decision": r.get("decision", "")} for r in self._shown_rows()]

        def work() -> None:
            try:
                found = self._loader(folder, rows)
            except Exception:
                found = (False, [], None)
            self._loaded.emit(token, found)

        threading.Thread(target=work, name="qml-best-of", daemon=True).start()

    def _arrived(self, token: int, found) -> None:
        if token != self._token:
            return                                    # another folder was opened, or the dialog asked again
        has, self._candidates, self._people = found
        self._ai_state = "ready" if has else "none"
        self.changed.emit()

    def _ai_info(self) -> dict:
        ready = self._ai_state == "ready"
        counts = ai_best_of.coverage(self._candidates)
        total = len(self._candidates)
        return {
            "state": self._ai_state, "ready": ready, "total": total, "people": len(self._people or {}),
            "why": "" if ready else ("Looking for this folder's saved AI results…" if self._ai_state == "loading" else ai_best_of.plan([], "", "", 1, has_report=False).problem),
            "strategies": [{"value": key, "label": label, "desc": desc} for key, label, desc in ai_best_of.STRATEGIES],
            "measures": [{"value": key, "label": f"{label} · {counts[key]} of {total} photos", "count": counts[key], "overall": overall}
                         for key, (label, overall, _up) in ai_best_of.MEASURES.items() if counts[key]],
            "actions": [{"value": "show", "label": "Show the picks"}, {"value": "win", "label": "Mark them as Winners"}]
                       + ([{"value": "collection", "label": "Add them to a collection"}] if self._collections is not None else []),
        }

    aiInfo = Property("QVariantMap", _ai_info, notify=changed)

    @Slot(str, result=str)
    def defaultMeasure(self, strategy: str) -> str:
        return ai_best_of.default_measure(self._candidates, strategy)

    def _plan(self, strategy: str, measure: str, size: int):
        return ai_best_of.plan(self._candidates, strategy, measure, size, has_report=self._ai_state == "ready", people=self._people)

    @Slot(str, str, int, result="QVariantMap")
    def preview(self, strategy: str, measure: str, size: int) -> dict:
        """What Build would choose, before anything is changed: how many photos, or why none, and what was left out."""
        found = self._plan(strategy, measure, size)
        return {"count": len(found.paths), "problem": found.problem, "note": found.note}

    @Slot(str, str, int, str, str, result=bool)
    def buildFromReport(self, strategy: str, measure: str, size: int, action: str, collection: str) -> bool:
        """Choose from the saved report, show the picks, and (when asked) mark them as Winners or add them to a collection as one
        undo step. Returns False, changing nothing, when the report cannot give this choice."""
        found = self._plan(strategy, measure, size)
        if found.problem or not found.paths:
            return False
        p = self._photos
        keys = {normalized_path_key(path) for path in found.paths}
        label = next(text for key, text, _desc in ai_best_of.STRATEGIES if key == strategy)
        self._summary = f"{label}, by {ai_best_of.MEASURES[measure][0]}: {len(found.paths)} photos from the saved AI results." + (f" {found.note}" if found.note else "")
        done = ""
        if action == "win":
            rows = [index for index in (p.row_for_path(path) for path in found.paths) if index >= 0 and (p.row_at(index) or {}).get("decision") != "win"]
            if rows:
                p.setDecisionMany(rows, "win")          # one step in the undo history (a single photo is one step too)
                done = f"{len(rows)} marked as Winners."
            else:
                done = "They were already Winners."
        elif action == "collection" and self._collections is not None and collection:
            done = self._add_to_collection(collection, list(found.paths))
        p.set_scope("best", strategy, lambda r, k=keys: normalized_path_key(r["path"]) in k)
        if done:
            self._summary += " " + done
            if self._toasts is not None:
                self._toasts.push("success", "Best photos chosen", done + " Ctrl+Z takes it back.")
        self.changed.emit()
        return True

    def _add_to_collection(self, cid: str, paths: list[str]) -> str:
        """Add the picks that are not in the collection yet, as one undo step that takes exactly those out again."""
        p, c = self._photos, self._collections
        rows = [index for index in (p.row_for_path(path) for path in paths) if index >= 0]
        fresh = [p.row_at(index)["path"] for index in rows if not c.hasRow(cid, index)]
        if not fresh:
            return "They were already in the collection."
        name = str((c.get(cid) or {}).get("name") or "the collection")
        c.addRows(cid, [p.row_for_path(path) for path in fresh])

        def undo() -> bool:
            for path in fresh:
                index = p.row_for_path(path)
                if index >= 0 and c.hasRow(cid, index):
                    c.removeRow(cid, index)
            return True

        p.push_undo(f"add {len(fresh)} photos to {name}", undo)
        return f"{len(fresh)} added to {name}."

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
