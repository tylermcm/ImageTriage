"""The Cull window's real analysis: the shared stack-first cascade (`cascade.task.CascadeRunTask`), and what it leaves behind.

`CullRun` starts a `CascadeRunTask` on the thread pool and reports its stage, its progress and its end to the Cull model (queued, so on the UI thread).
Every decision it makes is an `Outcome`; a run produces them from the task's `FolderResult`, a folder analysed earlier produces them from the saved
report (`ranked_clusters_export.csv`, read with `ai_results.load_ai_bundle`), so the window shows the same thing either way.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThreadPool, Signal

from ..cascade.folder import ROLE_BRACKET, ROLE_CUT, ROLE_DUPLICATE, ROLE_FINAL, ROLE_FLAGGED, ROLE_NO_PICK, ROLE_PICK, ROLE_REJECTED
from ..cascade.model import FLAG_BLINK, FLAG_BLOWN_OUT, FLAG_CLIPPED, FLAG_SOFT

FLAG_ORDER = (FLAG_BLINK, FLAG_SOFT, FLAG_CLIPPED, FLAG_BLOWN_OUT)      # the reason a frame is shown with when it has several
FLAGGED_ROLES = (ROLE_REJECTED, ROLE_FLAGGED, ROLE_NO_PICK)


@dataclass(slots=True, frozen=True)
class Outcome:
    role: str
    flags: tuple[str, ...]
    summary: str
    cluster: str            # frames of one stack share it
    stack_size: int
    stack_rank: int         # 1-based, best first
    ranked: bool            # a ranker ordered this frame's stack


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def outcomes_from_result(result) -> dict[str, Outcome]:
    """{path: Outcome} from a finished run's `FolderResult`."""
    out = {}
    for path, o in result.outcomes.items():
        cluster = f"b{-o.burst_index}" if o.burst_index < 0 else f"{o.burst_index}-{o.stack_index}"
        out[path] = Outcome(o.role, tuple(o.flags), o.summary, cluster, o.stack_size, o.stack_rank, o.rank is not None)
    return out


def outcomes_from_bundle(bundle, rows: list[dict]) -> dict[str, Outcome]:
    """{path: Outcome} for the photos of `rows` that a saved cascade report decided (an older kind of report, without cascade roles, gives none)."""
    from ..ai_results import find_ai_result_for_record

    out = {}
    if getattr(bundle, "native_document", None) is not None:
        for path, item in bundle.native_document["files"].items():
            decision = item["decision"]
            flag = {"reject": "native_reject", "review": "native_review"}.get(decision)
            out[path] = Outcome("native_" + decision, (flag,) if flag else (),
                                "; ".join(map(str, item.get("reasons", []))) or decision.capitalize(),
                                str(item.get("comparison_set") or "alone:" + path),
                                item.get("set_size") or 1, item.get("rank_in_set") or 1, False)
        return out
    for row in rows:
        result = find_ai_result_for_record(bundle, row["record"], preferred_path=row["path"]) if row.get("record") is not None else bundle.result_for_path(row["path"])
        if result is None or not result.cascade_role:
            continue
        flags = tuple(t.strip() for t in result.triggered_tags.split(",") if t.strip())
        out[row["path"]] = Outcome(result.cascade_role, flags, result.cascade_summary, result.group_id, result.group_size, result.rank_in_group,
                                   result.cascade_role == ROLE_PICK or "runner-up" in result.cascade_summary)
    return out


def reason_of(outcome: Outcome) -> str | None:
    """What the Cull window calls a flagged frame's problem: a blink, soft, clipped or blown out frame, or a near-duplicate; None for a frame it keeps."""
    if outcome.role == "native_review":
        return "native_review"
    if outcome.role == "native_reject":
        # The pinned native report supplies prose reasons rather than gate IDs.
        # Match its rejection phrases, not incidental ranking/score notes.
        text = outcome.summary.lower()
        rejected = text[text.find("rejected:"):] if "rejected:" in text else text
        if "below the quality floor" in rejected or rejected == "quality_floor":
            return "native_quality"
        if any(t in rejected for t in ("black frame", "blown to white", "sky blown out", "washed out", "unusable shadows")):
            return "native_exposure"
        if "featureless frame" in rejected:
            return "native_blank"
        if "not the best-exposed frame of bracket" in rejected:
            return "native_bracket"
        if "better unit" in rejected or "lost in its scene" in rejected:
            return "native_scene"
        if "better frame" in rejected and "burst" in rejected:
            return "native_burst"
        return "native_reject"
    if outcome.role == ROLE_DUPLICATE:
        return "duplicate"
    if outcome.role in FLAGGED_ROLES:
        return next((f for f in FLAG_ORDER if f in outcome.flags), FLAG_BLOWN_OUT if outcome.role == ROLE_NO_PICK else None)
    return None


def stacks_of(rows: list[dict], outcomes: dict[str, Outcome]) -> list[list[dict]]:
    """The photos grouped into their stacks (best first inside a stack); a photo the run could not read is a stack of one. Stacks follow the grid's order."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        o = outcomes.get(row["path"])
        groups.setdefault(o.cluster if o is not None else "alone:" + row["path"], []).append(row)
    stacks = []
    for members in groups.values():
        members.sort(key=lambda r: outcomes[r["path"]].stack_rank if r["path"] in outcomes else 0)
        stacks.append(members)
    return stacks


class CullRun(QObject):
    stage = Signal(int, float)          # which of the window's four stages, how far into it (0..1)
    finished = Signal(object, bool, bool)   # the FolderResult, whether the eyes were checked, whether the rankers ran (not: were asked for)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._task = None
        self._signals = None
        self._eyes = self._rank = False
        self._device = ""                                   # what the rankers were asked to run on ("gpu", "cpu")
        self._decided = 0.0                                 # how far the deciding stage has got: it never goes back
        self.running = False
        self.notes: list[str] = []                          # what the last finished run skipped or could not do, in its own words
        self.device_text = ""                               # where its rankers really ran, as their workers reported ("" when none ran)

    @property
    def alive(self) -> bool:
        """A task was started and has not said it ended yet (a cancelled one counts until it has really stopped)."""
        return self._task is not None

    def _current(self) -> bool:
        """Whether the signal being handled comes from the task now running. A signal a replaced task queued earlier is not acted on."""
        sender = self.sender()
        return sender is None or sender is self._signals

    def start(self, *, folder: str, records: list, cascade, blink_threshold: float, decision, protected: list[str]) -> None:
        if self._task is not None:
            # Two tasks at once would both report to this object, and the first one's late end would be taken for the second's.
            raise RuntimeError("The previous analysis is still stopping. Try again in a moment.")
        from ..aiculler_workflow import build_aiculler_workflow_paths
        from ..cascade.launch import default_scorer_factory
        from ..cascade.task import CascadeRunSettings, CascadeRunTask

        factory = None
        if decision.use_rankers or decision.use_blink:
            factory = default_scorer_factory(decision.device or "cpu", rankers=decision.use_rankers, blink=decision.use_blink, quality=decision.use_quality, appeal=decision.use_appeal)
        task = CascadeRunTask(folder=Path(folder), records=records, paths=build_aiculler_workflow_paths(folder),
                              settings=CascadeRunSettings(cascade=cascade, blink_threshold=blink_threshold), scorer_factory=factory,
                              protected_paths=protected, ranking_note=decision.note)
        self._task, self._signals = task, task.signals
        self._eyes, self._rank = decision.use_blink, decision.use_rankers
        self._device = decision.device if decision.use_rankers else ""
        self._decided = 0.0
        self.notes, self.device_text = [], ""
        queued = Qt.ConnectionType.QueuedConnection
        task.signals.progress.connect(self._progress, queued)
        task.signals.stage.connect(self._stage, queued)
        task.signals.finished.connect(self._finished, queued)
        task.signals.failed.connect(self._failed, queued)
        task.signals.cancelled.connect(self._cancelled, queued)
        self.running = True
        QThreadPool.globalInstance().start(task)

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    # the cascade has four stages (reading capture times, reading previews, deciding, writing); the window shows the eyes check and the ranking inside
    # "deciding", so the progress labels tell which of the window's stages is on
    def _stage(self, folder: str, index: int, total: int, label: str) -> None:
        if not self._current():
            return
        if index < 2:
            self._decided = 0.0
        self.stage.emit({0: 0, 1: 1, 2: 3, 3: 3}.get(index, 3), 0.0 if index < 3 else 0.97)

    def _progress(self, folder: str, label: str, done: int, total: int, eta: str) -> None:
        if not self._current():
            return
        fraction = done / max(1, total)
        low = label.lower()
        if "closed eyes" in low:
            self.stage.emit(2, fraction)
        elif low.startswith("reading capture"):
            self.stage.emit(0, fraction)
        elif low.startswith("reading previews"):
            self.stage.emit(1, fraction)
        elif low.startswith("deciding"):
            # The only steady signal of this stage is the share of bursts finished. The rankers and the people pass report their own 0 to 1 for every
            # stack or batch they are given, and feeding those to the bar made it hop up and down between 0 and 100% many times over.
            self._decided = max(self._decided, min(0.95, fraction))
            self.stage.emit(3, self._decided)

    def _finished(self, folder: str, report_dir: str, html: str) -> None:
        if not self._current():
            return
        task, self._task, self._signals = self._task, None, None
        self.running = False
        # What the run did, not what it was asked to do: a ranker that could not start, or failed part way, leaves `ranked_with` empty and a
        # note saying why.
        ranked = bool(getattr(task, "ranked_with", ())) if task is not None else False
        self.notes = [str(note) for note in getattr(task, "notes", ())] if task is not None else []
        reported = str(getattr(task, "ranking_device", "") or "") if task is not None else ""
        self.device_text = ""
        if ranked and reported:
            on_card = reported.lower().startswith("cuda")
            self.device_text = "the graphics card" if on_card else "the CPU"
            if self._device == "gpu" and not on_card:
                self.notes.append("Ranking ran on the CPU: the AI runtime could not use the graphics card, so it was much slower than estimated.")
        self.finished.emit(task.result if task is not None else None, bool(task and task.blink_checked), ranked)

    def _failed(self, folder: str, message: str) -> None:
        if not self._current():
            return
        self._task = self._signals = None
        self.running = False
        self.failed.emit(message)

    def _cancelled(self, folder: str, message: str) -> None:
        if not self._current():
            return
        self._task = self._signals = None
        self.running = False
        self.cancelled.emit()
