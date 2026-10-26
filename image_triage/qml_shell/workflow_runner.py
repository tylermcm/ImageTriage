"""Runs a handoff recipe for real: the Widgets app's own export, archive, copy and move, on worker threads, reporting progress to the transfer cards.

What a recipe does follows its content and transfer mode, as `export_jobs_controller.run_workflow_recipe` does it:
  Export (resized / converted / stripped copies)  -> `workflows.build_workflow_export_plan` + `WorkflowExportTask`, then optionally an archive of the outputs
  Bundles, transfer Archive                       -> `archive_ops.CreateArchiveTask` over every file of every bundle
  Bundles, transfer Copy or Move                  -> `file_ops.copy_paths` / `move_paths` per photo (into a folder per photo when the recipe groups them)
A move takes the saved decisions with the files, takes the photos out of the grid and can be undone with Ctrl+Z. Copy and move can be cancelled between
photos; export and archive run to the end (the Widgets tasks have no cancel). Nothing here asks a question: the cards ask before a move.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal

from ..archive_ops import CreateArchiveTask, archive_format_for_key
from ..file_ops import FileMove, copy_paths, move_paths, record_paths
from ..image_resize import ResizeSourceItem
from ..workflows import (
    RECIPE_TRANSFER_ARCHIVE,
    RECIPE_TRANSFER_MOVE,
    WorkflowExportTask,
    build_workflow_export_plan,
    workflow_archive_path,
    workflow_destination_dir,
    workflow_record_folder_name,
)


class _CopySignals(QObject):
    progress = Signal(int, int, str, int)       # done, total, the photo just handled, bytes handled so far
    finished = Signal(object)                   # [(record, moves)] for the photos handled, and whether it was cancelled
    failed = Signal(str)


class _TransferTask(QRunnable):
    """Copy or move whole bundles one photo at a time, with a stop between photos."""

    def __init__(self, items: list, move: bool) -> None:
        super().__init__()
        self.items = items                      # [(record, bundle paths, target dir)]
        self.move = move
        self.cancelled = False
        self.signals = _CopySignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        done_items: list = []
        failures: list = []
        bytes_done = 0
        total = len(self.items)
        for index, (record, bundle, target_dir) in enumerate(self.items, start=1):
            if self.cancelled:
                break
            try:
                size = sum(os.path.getsize(p) for p in bundle if os.path.exists(p))
                moves = move_paths(bundle, target_dir) if self.move else copy_paths(bundle, target_dir)
            except OSError as exc:
                failures.append((record.name, str(exc)))
                self.signals.progress.emit(index, total, record.name, bytes_done)
                continue
            bytes_done += size
            done_items.append((record, moves))
            self.signals.progress.emit(index, total, record.name, bytes_done)
        self.signals.finished.emit({"done": done_items, "failed": failures, "cancelled": self.cancelled})


class WorkflowRunner(QObject):
    progress = Signal(float, str, int)          # fraction 0..1, the photo being handled, bytes handled so far
    finished = Signal(dict)                     # {"kind", "count", "written", "failed", "notes", "cancelled", "moved"}
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._task = None
        self._signals = None
        self._ctx: dict = {}
        self.running = False
        self._bundle = record_paths

    # ---------------- starting ----------------
    def run(self, recipe, records: list, root: str, folder: str, include_companions: bool = True) -> bool:
        """Start the recipe on `records` (the scanner's ImageRecords of the photos shown). `include_companions` off takes only each photo's own file, not its
        RAW or JPEG partner and sidecars (the cards' switch). Returns False when it cannot start (and says why on `failed`)."""
        if self.running:
            self.failed.emit("A handoff is already running.")
            return False
        destination = workflow_destination_dir(recipe, root)
        if not records:
            self.failed.emit("There are no photos to hand off.")
            return False
        if not destination and recipe.transfer_mode != RECIPE_TRANSFER_ARCHIVE:
            self.failed.emit("Choose a destination folder for this recipe.")
            return False
        self._bundle = (lambda r: record_paths(r)) if include_companions else (lambda r: (r.path,))
        self._ctx = {"recipe": recipe, "records": records, "root": root, "destination": destination, "folder": folder, "count": len(records)}
        if recipe.uses_transform_export:
            return self._start_export(recipe, records, destination, root)
        if recipe.transfer_mode == RECIPE_TRANSFER_ARCHIVE:
            return self._start_archive([p for r in records for p in self._bundle(r) if os.path.exists(p)], workflow_archive_path(recipe, root), recipe.archive_format, folder or None, "archive")
        return self._start_transfer(recipe, records, destination)

    def cancel(self) -> bool:
        """Stop between photos (copy and move only)."""
        if isinstance(self._task, _TransferTask):
            self._task.cancelled = True
            return True
        return False

    @property
    def can_cancel(self) -> bool:
        return isinstance(self._task, _TransferTask)

    def _begin(self, task, kind: str) -> None:
        self._ctx["kind"] = kind
        self._task, self._signals = task, task.signals
        self.running = True
        QThreadPool.globalInstance().start(task)

    # ---------------- export ----------------
    @staticmethod
    def _export_source(record) -> ResizeSourceItem | None:
        candidates = [record.preferred_edit_path or "", record.path, *record.companion_paths, *record.edited_paths]
        source = next((p for p in candidates if p and os.path.exists(p)), "") or next((p for p in candidates if p), "")
        return ResizeSourceItem(source_path=source, source_name=Path(source).name) if source else None

    def _start_export(self, recipe, records: list, destination: str, root: str) -> bool:
        sources, seen = [], set()
        for record in records:
            item = self._export_source(record)
            key = os.path.normcase(os.path.normpath(item.source_path)) if item else ""
            if item is not None and key not in seen:
                seen.add(key)
                sources.append(item)
        if not sources:
            self.failed.emit("No exportable sources were available for the photos.")
            return False
        plan = build_workflow_export_plan(sources, recipe, destination_dir=destination)
        if not plan.can_apply:
            self.failed.emit(plan.general_error or "The export plan could not be built.")
            return False
        task = WorkflowExportTask(plan)
        task.signals.progress.connect(self._steps, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._export_done, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        self._begin(task, "export")
        return True

    def _export_done(self, outcome) -> None:
        recipe = self._ctx["recipe"]
        written = [p for p in outcome if isinstance(p, str)]
        self._ctx.update(written=len(written), failed=list(getattr(outcome, "failed", ())), notes=list(getattr(outcome, "notes", ())))
        self.running = False
        if recipe.archive_after_export and written:
            archive = workflow_archive_path(recipe, self._ctx["root"])
            if archive and self._start_archive(written, archive, recipe.archive_format, self._ctx["destination"] or None, "archive"):
                return
        self._finish()

    # ---------------- archive ----------------
    def _start_archive(self, paths: list, archive_path: str, key: str, root_dir, kind: str) -> bool:
        if not paths or not archive_path:
            self.failed.emit("There were no files to archive.")
            return False
        task = CreateArchiveTask(tuple(paths), archive_path, archive_key=key, root_dir=root_dir)
        task.signals.progress.connect(self._steps, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._archive_done, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        self._ctx["archive"] = archive_path
        self._ctx.setdefault("written", len(paths))
        self._begin(task, kind)
        return True

    def _archive_done(self, created: object) -> None:
        self._ctx["archive"] = str(created or self._ctx.get("archive", ""))
        self._ctx["destination"] = str(Path(self._ctx["archive"]).parent) if self._ctx.get("archive") else self._ctx["destination"]
        self.running = False
        self._finish()

    # ---------------- copy and move ----------------
    def _start_transfer(self, recipe, records: list, destination: str) -> bool:
        items = []
        for record in records:
            target = destination
            if recipe.group_by_record_folder:
                target = str(Path(destination) / workflow_record_folder_name(record.name))
            items.append((record, self._bundle(record), target))
        task = _TransferTask(items, move=recipe.transfer_mode == RECIPE_TRANSFER_MOVE)
        task.signals.progress.connect(self._transfer_step, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._transfer_done, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        self._begin(task, "move" if task.move else "copy")
        return True

    def _transfer_step(self, done: int, total: int, name: str, bytes_done: int) -> None:
        self.progress.emit(done / max(1, total), name, bytes_done)

    def _transfer_done(self, result: dict) -> None:
        self._ctx.update(written=len(result["done"]), failed=result["failed"], cancelled=result["cancelled"], moved=result["done"] if self._ctx["kind"] == "move" else [], notes=[])
        self.running = False
        self._finish()

    # ---------------- shared ----------------
    def _steps(self, current: int, total: int, message: str) -> None:
        self.progress.emit(current / max(1, total), message, 0)

    def _failed(self, message: str) -> None:
        self.running = False
        self._task = self._signals = None
        self.failed.emit(message)

    def _finish(self) -> None:
        ctx = self._ctx
        self._task = self._signals = None
        self.finished.emit({"kind": ctx.get("kind", ""), "count": ctx.get("count", 0), "written": ctx.get("written", 0), "failed": ctx.get("failed", []), "notes": ctx.get("notes", []),
                            "cancelled": ctx.get("cancelled", False), "moved": ctx.get("moved", []), "destination": ctx.get("destination", ""), "archive": ctx.get("archive", "")})
