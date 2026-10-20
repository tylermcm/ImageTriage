"""The Analyze run for the stack-first cascade: a QRunnable that reads previews, decides every frame and writes the results the app already loads.

It emits the same signals as the older AI Culler task (started, stage, progress, detail, finished, failed, cancelled) and writes the same ranked-export
files, so ``AIRunController`` and the results loader work on it unchanged. The cheap passes need no AI runtime; the two rankers run only when a scorer
factory is supplied, and a ranker that cannot start (no runtime, no weights) is skipped with a note instead of failing the run.
"""

from __future__ import annotations

import os
import time
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QObject, QRunnable, Signal

from ..ai_workflow import AIWorkflowPaths
from ..brackets import BracketDetector
from ..metadata import load_capture_metadata
from ..models import ImageRecord
from .blink import BlinkScoreCache, is_blink
from .export import build_rows, write_export
from .extract import FeatureCache, extract_features
from .faces import FaceCache, FaceFinding
from .folder import ROLE_BRACKET, ROLE_DUPLICATE, ROLE_FINAL, FolderFrame, FolderResult, FrameOutcome, run_folder
from .model import CascadeSettings
from .pipeline import Scorer

STAGES = ("Reading capture times", "Reading previews", "Grouping and cleaning up", "Writing results")
FEATURE_CACHE_FILENAME = "cascade_features.json"
BLINK_CACHE_FILENAME = "cascade_blink.json"
BLINK_MODEL_ID = "mediapipe-face-landmarker-float16"  # change when the face model or how its score is computed changes
FACES_CACHE_FILENAME = "faces_cache.json"  # shared with the People index (face_stack.face_cache_path): a file analysed by either is not analysed again
FACES_BATCH = 16


class CascadeRunSignals(QObject):
    started = Signal(str)
    stage = Signal(str, int, int, str)
    progress = Signal(str, str, int, int, str)
    detail = Signal(str, str)
    finished = Signal(str, str, str)
    failed = Signal(str, str)
    cancelled = Signal(str, str)


@dataclass(slots=True)
class Scorers:
    """The two rankers (either may be missing) and a ``close`` that stops their worker processes."""

    maniqa: Scorer | None = None
    aesthetic: Scorer | None = None
    blink: Scorer | None = None  # how closed the eyes are per frame (0 open .. 1 closed); thresholded by blink.is_blink
    faces: Scorer | None = None  # the face finder (see faces.py): a WorkerRanker, so ``faces.details[key]`` holds each frame's faces after a call
    names: tuple[str, ...] = ()
    close: Callable[[], None] = lambda: None


# Builds the rankers for one run. It receives the task's cancel check and a progress callback (done, total, label).
ScorerFactory = Callable[[Callable[[], bool], Callable[[int, int, str], None]], Scorers]


class _Cancelled(RuntimeError):
    pass


@dataclass(slots=True)
class CascadeRunSettings:
    cascade: CascadeSettings = field(default_factory=CascadeSettings)
    max_workers: int | None = None


class CascadeRunTask(QRunnable):
    def __init__(
        self,
        *,
        folder: Path,
        records: Sequence[ImageRecord],
        paths: AIWorkflowPaths,
        settings: CascadeRunSettings | None = None,
        scorer_factory: ScorerFactory | None = None,
        protected_paths: Sequence[str] = (),
        ranking_note: str = "",
    ) -> None:
        super().__init__()
        self.folder = folder
        self.records = tuple(record for record in records if not record.is_folder)
        self.paths = paths
        self.settings = settings or CascadeRunSettings()
        self.scorer_factory = scorer_factory
        self.protected = frozenset(os.path.normcase(os.path.abspath(p)) for p in protected_paths if str(p).strip())
        self.ranking_note = ranking_note
        self.signals = CascadeRunSignals()
        self.setAutoDelete(True)
        self._cancel_requested = False
        self.result: FolderResult | None = None
        self.blink_checked = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def _cancelled(self) -> bool:
        return self._cancel_requested

    def run(self) -> None:
        folder_text = str(self.folder)
        scorers = Scorers()
        try:
            self.signals.started.emit(folder_text)
            frames = self._read_frames(folder_text)
            result, scorers, notes = self._decide(folder_text, frames)
            self._check_cancelled()
            self.signals.stage.emit(folder_text, 3, len(STAGES), STAGES[3])
            rows = build_rows(result.outcomes, [r.path for r in self.records], _capture_strings(frames))
            written = write_export(self.paths.report_dir, rows, result, ranked_with=scorers.names, note=" ".join(notes), blink_checked=self.blink_checked)
            self.result = result
            self.signals.finished.emit(folder_text, str(self.paths.report_dir), str(written["html"]))
        except _Cancelled:
            self.signals.cancelled.emit(folder_text, "AI review stopped")
        except Exception as error:  # noqa: BLE001 - reported to the UI, with the traceback in the detail log
            self.signals.detail.emit(folder_text, traceback.format_exc())
            self.signals.failed.emit(folder_text, str(error) or error.__class__.__name__)
        finally:
            try:
                scorers.close()
            except Exception:  # noqa: BLE001
                pass

    # -- steps ---------------------------------------------------------------------------------------------------------------------------------

    def _check_cancelled(self) -> None:
        if self._cancel_requested:
            raise _Cancelled()

    def _progress(self, folder: str, label: str, done: int, total: int, started: float) -> None:
        elapsed = time.perf_counter() - started
        eta = ""
        if done > 0 and total > done:
            remaining = elapsed / done * (total - done)
            eta = f"{int(remaining // 60)}m {int(remaining % 60):02d}s left" if remaining >= 60 else f"{int(remaining)}s left"
        self.signals.progress.emit(folder, label, done, total, eta)

    def _read_frames(self, folder: str) -> list[FolderFrame]:
        records = list(self.records)
        total = len(records)
        self.signals.stage.emit(folder, 0, len(STAGES), STAGES[0])
        started = time.perf_counter()
        detector = BracketDetector()
        captured: dict[str, float | None] = {}
        focal: dict[str, float | None] = {}
        for index, record in enumerate(records):
            self._check_cancelled()
            metadata = load_capture_metadata(record.path)
            detector.remember(metadata)
            value = metadata.captured_at_value
            captured[record.path] = value.timestamp() if value is not None else None
            focal_value = getattr(metadata, "focal_length_value", None)
            focal[record.path] = float(focal_value) if focal_value else None
            if index % 25 == 0 or index == total - 1:
                self._progress(folder, STAGES[0], index + 1, total, started)

        bracket_of: dict[str, int] = {}
        next_id = 0
        for index in range(total):
            if records[index].path in bracket_of:
                continue
            group = detector.group_for(records, index)
            if group is None:
                continue
            for member in records[group.start_index : group.end_index]:
                bracket_of[member.path] = next_id
            next_id += 1

        self.signals.stage.emit(folder, 1, len(STAGES), STAGES[1])
        started = time.perf_counter()
        cache = FeatureCache(self.paths.artifacts_dir / FEATURE_CACHE_FILENAME)
        extracted = extract_features(
            [record.path for record in records],
            cache=cache,
            progress=lambda done, count: self._progress(folder, STAGES[1], done, count, started),
            cancelled=self._cancelled,
            max_workers=self.settings.max_workers,
        )
        try:
            cache.save()
        except OSError:
            pass
        self._check_cancelled()
        if extracted.stats.failed:
            self.signals.detail.emit(folder, f"{extracted.stats.failed} frames could not be read and were left unrated.")

        frames = [
            FolderFrame(
                record.path,
                replace(feature, focal_mm=focal.get(record.path)) if feature is not None else None,
                captured.get(record.path),
                bracket_of.get(record.path),
            )
            for record, feature in zip(records, extracted.features)
        ]
        # Burst detection needs capture order, not display order.
        frames.sort(key=lambda frame: (frame.captured is None, frame.captured or 0.0, frame.path.casefold()))
        return frames

    def _decide(self, folder: str, frames: list[FolderFrame]) -> tuple[FolderResult, Scorers, list[str]]:
        self.signals.stage.emit(folder, 2, len(STAGES), STAGES[2])
        started = time.perf_counter()
        notes: list[str] = []
        if self.ranking_note:
            notes.append(self.ranking_note)
        scorers = Scorers()
        if self.scorer_factory is not None:
            try:
                scorers = self.scorer_factory(
                    self._cancelled,
                    lambda done, total, label: self.signals.progress.emit(folder, label, done, total, ""),
                )
            except Exception as error:  # noqa: BLE001
                notes.append(f"Ranking skipped: {error}")
                self.signals.detail.emit(folder, notes[-1])

        def progress(done: int, total: int) -> None:
            self._progress(folder, STAGES[2], done, total, started)

        frames = self._check_blinks(folder, frames, scorers, notes)
        try:
            result = self._run(frames, scorers, progress)
        except _Cancelled:
            raise
        except Exception as error:  # noqa: BLE001
            if type(error).__name__.startswith("Ranker") and type(error).__name__ != "RankerCancelled" and (scorers.maniqa or scorers.aesthetic):
                notes.append(f"Ranking skipped: {error}")
                self.signals.detail.emit(folder, notes[-1])
                scorers = Scorers()
                result = self._run(frames, scorers, progress)
            elif type(error).__name__ == "RankerCancelled":
                raise _Cancelled() from error
            else:
                raise
        if result.cancelled:
            raise _Cancelled()
        self._apply_protection(result)
        self._index_people(folder, result, scorers, notes)
        return result, scorers, notes

    def _index_people(self, folder: str, result: FolderResult, scorers: Scorers, notes: list[str]) -> None:
        """Find and embed the faces of every frame that survived the near-duplicate collapse, so the People window fills while the folder is being culled.

        A frame collapsed into a near-identical neighbour (or kept in an exposure bracket) shows the same people as the frame it was folded into, so it is
        skipped: that is most of the saving. Everything else is analysed once; the People index reads the shared cache. Never fatal: without the face models
        the run simply does not update People."""
        if scorers.faces is None:
            return
        keys = [path for path, outcome in result.outcomes.items() if outcome.role not in (ROLE_DUPLICATE, ROLE_BRACKET)]
        cache = FaceCache(self.paths.artifacts_dir / FACES_CACHE_FILENAME)
        missing = [key for key in keys if cache.get(key) is None]
        if not missing:
            return
        started = time.perf_counter()
        done = 0
        try:
            for start in range(0, len(missing), FACES_BATCH):
                self._check_cancelled()
                batch = missing[start:start + FACES_BATCH]
                scorers.faces(batch)
                failures = getattr(scorers.faces, "failures", {})
                for key in batch:
                    if key in failures:
                        continue  # unreadable: retried next time
                    detail = getattr(scorers.faces, "details", {}).get(key, {})
                    cache.put(key, [FaceFinding.from_json(item) for item in detail.get("faces", [])])
                done += len(batch)
                self._progress(folder, "Finding people", done, len(missing), started)
        except _Cancelled:
            raise
        except Exception as error:  # noqa: BLE001
            if type(error).__name__ == "RankerCancelled":
                raise _Cancelled() from error
            notes.append(f"People were not updated: {error}")
            self.signals.detail.emit(folder, notes[-1])
        finally:
            try:
                cache.save()
            except OSError:
                pass

    def _check_blinks(self, folder: str, frames: list[FolderFrame], scorers: Scorers, notes: list[str]) -> list[FolderFrame]:
        """Set each readable frame's blink flag from the eyes-closed scorer. A scorer that cannot run is skipped with a note, not fatal."""
        if scorers.blink is None:
            return frames
        keys = [f.path for f in frames if f.features is not None and f.bracket_id is None]
        if not keys:
            return frames
        cache = BlinkScoreCache(self.paths.artifacts_dir / BLINK_CACHE_FILENAME, BLINK_MODEL_ID)
        scores = {key: cached for key in keys if (cached := cache.get(key)) is not None}
        missing = [key for key in keys if key not in scores]
        if missing:
            try:
                fresh = scorers.blink(missing)
            except Exception as error:  # noqa: BLE001
                if type(error).__name__ == "RankerCancelled":
                    raise _Cancelled() from error
                notes.append(f"Eyes-closed check skipped: {error}")
                self.signals.detail.emit(folder, notes[-1])
                scorers.blink = None
                return frames
            for key, value in fresh.items():
                cache.put(key, value)
            scores.update(fresh)
            try:
                cache.save()
            except OSError:
                pass
        self.blink_checked = True
        return [
            replace(f, features=replace(f.features, blink=is_blink(scores.get(f.path, 0.0))))
            if f.features is not None and f.bracket_id is None
            else f
            for f in frames
        ]

    def _run(self, frames: list[FolderFrame], scorers: Scorers, progress: Callable[[int, int], None]) -> FolderResult:
        return run_folder(
            frames,
            self.settings.cascade,
            maniqa=scorers.maniqa,
            aesthetic=scorers.aesthetic,
            progress=progress,
            cancelled=self._cancelled,
        )

    def _apply_protection(self, result: FolderResult) -> None:
        """A frame the person already marked as a winner is never flagged, collapsed or cut."""
        if not self.protected:
            return
        for path, outcome in list(result.outcomes.items()):
            if os.path.normcase(os.path.abspath(path)) in self.protected and outcome.role != ROLE_FINAL:
                result.outcomes[path] = FrameOutcome(
                    path, ROLE_FINAL, outcome.flags, "Kept: you already marked this frame as a winner.", outcome.burst_index, outcome.burst_size,
                    outcome.stack_index, outcome.stack_count, outcome.stack_size, outcome.stack_rank, outcome.split_jump, None,
                )


def _capture_strings(frames: Sequence[FolderFrame]) -> dict[str, str]:
    from datetime import datetime

    out: dict[str, str] = {}
    for frame in frames:
        if frame.captured is not None:
            out[frame.path] = datetime.fromtimestamp(frame.captured).isoformat(timespec="seconds")
    return out
