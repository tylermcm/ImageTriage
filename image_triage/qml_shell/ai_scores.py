"""The AI's verdict on each photo of the open folder, for the inspector's AI tab and the grid's "AI score" sort.

The Widgets app writes a ranked report beside a folder it has analyzed (`ai_workflow.existing_hidden_ai_report_dir`, the `ranked_clusters_export.csv`
inside it) and reads it back whenever the folder is opened. This reads the same file, with the same parser (`ai_results.load_ai_bundle`), on a worker
thread each time the shell opens a folder, and answers from memory afterwards. A folder that has never been analyzed has no report: `state` says
so ("none") and the inspector offers to analyse it. Nothing here runs the analysis (that is the Cull window's job, docs/qml_unwired.md).

`aiScores.get(path)` is what QML reads: {"has": false, "state": "loading" | "none" | "missing"} or
{"has": true, "score", "call", "good", "line", "reasons": [{"good", "head", "detail"}], "group": [{"path", "row", "current"}], "size"}.
The score is the one the Widgets badge shows (the photo's percentile in its folder). The wording is `ai_why`'s and `ai_results`'.
"""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import Property, QObject, Signal, Slot

GROUP_SHOWN = 4                 # members of the photo's AI group the inspector shows (the photo itself is always one of them)
NO_GROUP = "unassigned"


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def load_bundle(folder: str):
    """The folder's saved AI results, or None when it has none (or they cannot be read). Slow: only called from a worker thread."""
    from ..ai_results import load_ai_bundle
    from ..ai_workflow import existing_hidden_ai_report_dir

    try:
        report_dir = existing_hidden_ai_report_dir(folder)
        return None if report_dir is None else load_ai_bundle(report_dir)
    except Exception:
        return None


def display_score(result) -> int:
    """0..100: the folder percentile the Widgets badge shows, else the within-group score, else the raw score as a percentage."""
    for value in (result.folder_percentile, result.normalized_score):
        if value is not None:
            return int(round(max(0.0, min(100.0, value))))
    return int(round(max(0.0, min(100.0, result.score * 100.0))))


class AiScores(QObject):
    revisionChanged = Signal()
    _loaded = Signal(int, object, object)

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None, loader=None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._memory = memory                       # tests and --mock: nothing is read from the disk
        self._loader = loader or load_bundle
        self._bundle = None
        self._state = "none"
        self._token = 0
        self._revision = 0
        self._loaded.connect(self._arrived)
        if not memory:
            photos.folderChanged.connect(self.reload)

    revision = Property(int, lambda self: self._revision, notify=revisionChanged)

    # -- loading -----------------------------------------------------------------------------------------------------------
    @Slot()
    def reload(self) -> None:
        """Read the open folder's saved AI results again (a new folder was opened, or an analysis has just finished)."""
        folder = self._photos.folder_dir()
        self._token += 1
        token = self._token
        if not folder:
            self._bundle, self._state = None, "none"
            self._bump()
            return
        self._state = "loading"
        self._bump()
        rows = self._photos.all_rows()

        def work() -> None:
            bundle = self._loader(folder)
            scores = {}                                                 # the score of each photo, worked out here so a big folder does not stall the window
            for row in rows if bundle is not None else ():
                result = self._result_for(bundle, row["path"], row.get("record"))
                if result is not None:
                    scores[row["path"]] = display_score(result)
            self._loaded.emit(token, bundle, scores)

        threading.Thread(target=work, name="qml-ai-scores", daemon=True).start()

    def _arrived(self, token: int, bundle, scores: dict) -> None:
        if token != self._token:
            return                                  # another folder was opened while this one was being read
        self._bundle = bundle
        self._state = "ready" if bundle is not None else "none"
        self._photos.rescore(lambda row: scores.get(row["path"]))
        self._bump()

    def _bump(self) -> None:
        self._revision += 1
        self.revisionChanged.emit()

    # -- lookups -----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _result_for(bundle, path: str, record=None):
        if bundle is None:
            return None
        if record is not None:
            from ..ai_results import find_ai_result_for_record

            return find_ai_result_for_record(bundle, record, preferred_path=path)
        return bundle.result_for_path(path)

    @Slot(str, result="QVariantMap")
    def get(self, path: str) -> dict:
        if self._state == "loading":
            return {"has": False, "state": "loading"}
        if self._bundle is None:
            return {"has": False, "state": "none"}
        index = self._photos.row_for_path(path)
        row = self._photos.row_at(index) if index >= 0 else None
        result = self._result_for(self._bundle, path, row.get("record") if row else None)
        if result is None:
            return {"has": False, "state": "missing"}
        return self._describe(result)

    def _describe(self, result) -> dict:
        from ..ai_results import AIConfidenceBucket
        from ..ai_why import build_ai_why

        good = result.confidence_bucket in (AIConfidenceBucket.OBVIOUS_WINNER, AIConfidenceBucket.LIKELY_KEEPER)
        why = build_ai_why(result)
        reasons = [{"good": good, "head": line.rstrip(".") + ".", "detail": ""} for line in (why.reasons if why else ())]
        said = {r["head"].casefold() for r in reasons}
        reasons += [{"good": False, "head": flag + ".", "detail": ""} for flag in (why.flags if why else ()) if (flag + ".").casefold() not in said]
        grouped = result.group_size > 1 and result.group_id != NO_GROUP
        parts = [f"#{result.rank_in_group} of {result.group_size} in its group"] if grouped else []
        if why and why.category:
            parts.append(why.category)
        return {
            "has": True, "state": "ready", "score": display_score(result), "call": result.confidence_bucket_label, "good": good,
            "line": " · ".join(parts) or "Scored on its own",
            "reasons": reasons, "size": result.group_size if grouped else 1,
            "group": self._group_of(result) if grouped else [],
        }

    def _group_of(self, result) -> list[dict]:
        members = list(self._bundle.group_results(result.group_id)) if self._bundle else []
        me = _key(result.file_path)
        mine = next((m for m in members if _key(m.file_path) == me), None)
        shown = members[:GROUP_SHOWN]
        if mine is not None and mine not in shown:
            shown = shown[:-1] + [mine]                     # the photo itself is always among the ones shown
        return [{"path": m.file_path, "row": self._photos.row_for_path(m.file_path), "current": _key(m.file_path) == me} for m in shown]
