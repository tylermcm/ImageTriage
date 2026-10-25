"""The Move / Copy To Folder cards and the handoff's progress, for the QML shell.

Wording follows the mockup (and the Widgets app's `ui/transfer_dialogs.py`): "Copy 12 items?", "Copying 12 items", "Copied 12 items".
A handoff run is real: when `handoff_model` prepared a job (a recipe and real photos) the cards run it through `workflow_runner.WorkflowRunner` and follow
its progress; a move or an archive asks first, as the Widgets app does. Without a job (`--mock`, the cards' test hook) `start()` plays a scripted progress
and no file is touched. See docs/qml_unwired.md.
"""
from __future__ import annotations

import math
import os
import time

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

_STEP = 0.018                 # of the whole per tick
_TICK_MS = 110
_SAMPLE_BYTES = int(1.4 * 1024 ** 3)


def _size(n: float) -> str:
    if n < 1048576:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1048576:.0f} MB" if n < 1024 ** 3 else f"{n / 1024 ** 3:.1f} GB"


class TransferModel(QObject):
    cardChanged = Signal()

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None, toasts=None, runner=None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._toasts = toasts
        self._runner = runner               # workflow_runner.WorkflowRunner, or None (the cards then play a scripted progress)
        self._job = None                    # what handoff_model prepared for the next run: {"recipe", "records", "root", "folder"}
        self._real = False                  # the cards are following a real run
        self._started = 0.0
        self._bytes = 0
        self._now_name = ""
        if runner is not None:
            runner.progress.connect(self._run_progress)
            runner.finished.connect(self._run_finished)
            runner.failed.connect(self._run_failed)
        self._memory = memory               # --mock and tests: the sample size (1.4 GB) instead of the files' own
        self._state = ""            # "", "confirm", "progress", "done"
        self._kind = "copy"         # copy, move, export, archive; "wf" while asking about a destructive workflow
        self._move = False
        self._count = 0
        self._from = ""
        self._to = ""
        self._comp = True
        self._p = 0.0
        self._cancel = False
        self._total = _SAMPLE_BYTES
        self._names: list[str] = []
        self._card: dict = {}
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        photos.countsChanged.connect(self._photos_changed)

    def _photos_changed(self) -> None:
        if self._state:
            self._names = []
            self._rebuild()

    # -- entry points -------------------------------------------------------------------------------------------------------------
    @Slot(str, int, str, str)
    def ask(self, kind: str, count: int, from_dir: str, to_dir: str) -> None:
        """Move / Copy To Folder: confirm first."""
        self._begin(kind, count, from_dir, to_dir)
        self._state = "confirm"
        self._rebuild()

    @Slot(str, int, str, str)
    def handoff(self, kind: str, count: int, from_dir: str, to_dir: str) -> None:
        """A handoff recipe was run: a recipe that moves bundles asks first, anything else starts at once."""
        self._begin(kind, count, from_dir, to_dir)
        if kind == "move":
            self._kind = "wf"
            self._state = "confirm"
            self._rebuild()
        else:
            self._start()

    def _scan(self) -> None:
        """File names and total size from the photos the grid shows (the sample 1.4 GB when the files are not there)."""
        total, names = 0, []
        for index in range(min(self._count, self._photos.shown)):
            row = self._photos.get(index)
            names.append(str(row.get("name", "")) + ("." + str(row.get("ext", "")) if row.get("ext") else ""))
            try:
                total += os.path.getsize(row.get("path", ""))
            except OSError:
                total = 0
                break
        self._names = [n for n in names if n]
        self._total = _SAMPLE_BYTES if self._memory else (total or _SAMPLE_BYTES)

    def _begin(self, kind: str, count: int, from_dir: str, to_dir: str) -> None:
        self._timer.stop()
        self._kind, self._move = kind, kind == "move"
        self._count, self._from, self._to = max(1, count), from_dir, to_dir
        self._p, self._cancel, self._comp = 0.0, False, True
        self._names = []

    def setJob(self, job) -> None:
        """What the next handoff run does (None for sample photos): set by `handoff_model` just before it asks for the cards."""
        self._job = job

    def _start(self) -> None:
        self._state, self._p, self._cancel = "progress", 0.0, False
        job, self._job = self._job, None
        if job is not None and self._runner is not None and not self._memory:
            self._real, self._started, self._bytes, self._now_name = True, time.monotonic(), 0, ""
            self._timer.stop()
            self._rebuild()
            if not self._runner.run(job["recipe"], job["records"], job["root"], job["folder"], self._comp):
                self._real = False            # it said why on `failed`
            return
        self._real = False
        self._timer.start()
        self._rebuild()

    # -- following a real run -----------------------------------------------------------------------------------------------------
    def _run_progress(self, fraction: float, name: str, bytes_done: int) -> None:
        if self._real and self._state == "progress":
            self._p, self._now_name, self._bytes = min(1.0, fraction), name, bytes_done
            self._rebuild()

    def _run_failed(self, message: str) -> None:
        self._timer.stop()
        self._real = False
        self._state = ""
        self._rebuild()
        if self._toasts is not None:
            self._toasts.push("error", "The handoff did not run", message)

    def _run_finished(self, result: dict) -> None:
        if not self._real:
            return
        self._real = False
        moved = result.get("moved") or []
        if moved:
            self._after_move(moved)
        failed = result.get("failed") or []
        if failed and self._toasts is not None:
            self._toasts.push("warn", f"{len(failed)} {'photo was' if len(failed) == 1 else 'photos were'} left out", f"{failed[0][0]}: {failed[0][1]}")
        if result.get("cancelled"):
            self._state = ""
            self._rebuild()
            if self._toasts is not None:
                self._toasts.push("info", f"Stopped after {result.get('written', 0)} of {result.get('count', 0)}", "The ones already done stay where they are.")
            return
        self._count = max(1, int(result.get("written") or self._count))
        if result.get("archive"):
            self._to = result["archive"]
        elif result.get("destination"):
            self._to = result["destination"]
        self._p, self._state = 1.0, "done"
        self._rebuild()
        if not moved:
            self._photos.reload() if self._folder_touched(result) else None

    def _folder_touched(self, result: dict) -> bool:
        """Did the run write into the folder that is open (then it is read again)?"""
        here = self._photos.folder_dir()
        target = result.get("destination") or ""
        return bool(here and target and os.path.normcase(os.path.normpath(target)).startswith(os.path.normcase(os.path.normpath(here))))

    def _after_move(self, moved: list) -> None:
        """A move: the saved decisions follow their photos, the photos leave the grid, and one Undo moves them all back."""
        from .decision_bridge import session_id
        from .file_actions import rekey_after_move

        rows = {os.path.normcase(os.path.normpath(r["path"])): r for r in self._photos.all_rows()}
        for record, moves in moved:
            row = rows.get(os.path.normcase(os.path.normpath(record.path)))
            rekey_after_move(record, moves, row.get("annotation") if row else None)
        self._photos.remove_paths([record.path for record, _ in moved])
        self._photos.push_undo(f"handoff of {len(moved)} photos", lambda: self._undo_move(moved, rows))

    def _undo_move(self, moved: list, rows: dict) -> bool:
        import shutil
        from pathlib import Path

        from .file_actions import rekey_after_move

        try:
            for record, moves in moved:
                for move in moves:
                    Path(move.source_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(move.target_path, move.source_path)
        except OSError as exc:
            if self._toasts is not None:
                self._toasts.push("error", "Could not move them back", str(exc))
            return False
        for record, moves in moved:
            row = rows.get(os.path.normcase(os.path.normpath(record.path)))
            annotation = row.get("annotation") if row else None
            if annotation is not None and not annotation.is_empty:
                from ..decision_store import DecisionStore
                from .decision_bridge import session_id

                new_path = next((m.target_path for m in moves if m.source_path == record.path), "")
                if new_path:
                    DecisionStore().move_annotation(session_id(), new_path, record, annotation)
        self._photos.reload()
        if self._toasts is not None:
            self._toasts.push("success", f"Moved {len(moved)} photos back")
        return True

    # -- buttons --------------------------------------------------------------------------------------------------------------------
    @Slot()
    def confirm(self) -> None:
        if self._state != "confirm":
            return
        if self._kind == "wf":
            self._kind, self._move = "move", True
        self._start()

    @Slot()
    def dismiss(self) -> None:
        self._timer.stop()
        self._state = ""
        self._rebuild()

    @Slot()
    def cancel(self) -> None:
        if self._state == "progress":
            self._cancel = True
            if self._real and self._runner is not None:
                self._runner.cancel()
            self._rebuild()

    @Slot(bool)
    def setCompanions(self, on: bool) -> None:
        self._comp = on
        self._rebuild()

    @Slot()
    def openDestination(self) -> None:
        if self._to and os.path.isdir(self._to):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._to))
        self.dismiss()

    @Slot(str, str, int, float)
    def show(self, state: str, kind: str, count: int, progress: float) -> None:
        """Test hook: put the cards in a state (`--ui txState=progress;txKind=copy;txP=0.42`)."""
        self._begin(kind if kind != "wf" else "move", count, self._photos.folderPath or "D:\\Photos\\2026\\Georgia trip\\Kutaisi",
                    "E:\\Cards\\Kutaisi, Georgia")
        if kind == "wf":
            self._kind = "wf"
        self._state, self._p = state, progress
        self._timer.stop()
        self._rebuild()

    def _tick(self) -> None:
        if self._cancel:
            self.dismiss()
            return
        self._p = min(1.0, self._p + _STEP)
        if self._p >= 1.0:
            self._timer.stop()
            self._state = "done"
        self._rebuild()

    # -- the card -----------------------------------------------------------------------------------------------------------------------
    def _rebuild(self) -> None:
        if not self._names:
            self._scan()
        verb = {"export": "Exporting", "archive": "Archiving", "move": "Moving"}.get(self._kind, "Copying")
        past = {"export": "Exported", "archive": "Archived", "move": "Moved"}.get(self._kind, "Copied")
        imperative = "Move" if self._move or self._kind == "move" else "Copy"
        n = self._count
        items = f"{n} item{'s' if n != 1 else ''}"
        images = f"{n} image{'s' if n != 1 else ''}"
        left_frac = max(0.0, 1.0 - self._p)
        done_items = math.floor(n * self._p)
        seconds = max(0, round(left_frac * 6))
        wf = self._kind == "wf"
        real = self._real
        if real:
            elapsed = max(0.001, time.monotonic() - self._started)
            seconds = round(elapsed * (1 - self._p) / self._p) if self._p > 0.02 else 0
            speed = f"{self._bytes / elapsed / 1048576:.1f} MB/s" if self._bytes else ""
            done_items = math.floor(n * self._p)
        self._card = {
            "state": self._state,
            "title": "Run destructive workflow?" if wf else f"{imperative} {items}?",
            "body": "This workflow moves the source bundles into the destination structure. Continue?" if wf else f"{imperative} {images} to:",
            "hasPath": not wf, "path": self._to, "hasCompanions": not wf, "companions": self._comp,
            "yes": "Move bundles" if wf else imperative, "danger": wf,
            "headline": f"{verb} {items}", "route": f"{self._from} \u2192 {self._to}",
            "pct": round(self._p * 100), "pctText": f"{round(self._p * 100)}% complete",
            "speed": f"{78 + 9 * math.sin(self._p * 14):.1f} MB/s" if 0 < self._p < 1 else "",
            "name": self._names[min(len(self._names) - 1, done_items)] if self._names else "",
            "eta": "Less than a second" if seconds < 2 else f"About {seconds} seconds",
            "left": f"{n - done_items} ({_size(self._total * left_frac)})",
            "canCancel": True,
            "cancelLabel": "Cancelling\u2026" if self._cancel else "Cancel",
            "doneTitle": f"{past} {items}", "doneBody": f"{past} {images} to:",
        }
        if real:
            self._card.update(speed=speed, name=self._now_name, eta="Less than a second" if seconds < 2 else f"About {seconds} seconds", left=str(max(0, n - done_items)),
                              canCancel=bool(self._runner is not None and self._runner.can_cancel))
        self.cardChanged.emit()

    card = Property("QVariantMap", lambda self: self._card, notify=cardChanged)
    active = Property(bool, lambda self: self._state != "", notify=cardChanged)
