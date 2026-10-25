"""Batch rename, resize and convert for the QML shell.

The plans are built by the Widgets app's own planners (`batch_rename.build_batch_rename_preview`, `image_resize.build_resize_plan`,
`image_convert.build_convert_plan`), so names, statuses, error messages and what blocks Apply are identical. The scope is every photo the
grid shows. **Applying is real** (not for the sample photos of `--mock`): rename runs the Widgets' `BatchRenameApplyTask`, resize and convert their
`ResizeApplyTask` / `ConvertApplyTask`, on a worker thread with a toast that follows the progress. A rename moves the saved decisions with the files
and can be undone (Ctrl+Z); resize and convert write copies (or overwrite, if the option says so) and have no undo, as in the Widgets app.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Property, QObject, Qt, QThreadPool, Signal, Slot

from ..batch_rename import BatchRenameRules, RenameCaseMode, build_batch_rename_preview
from ..image_convert import ConvertOptions, ConvertSourceItem, build_convert_plan, convert_formats, format_for_suffix
from ..image_resize import ResizeOptions, ResizeSourceItem, build_resize_plan, preset_for_key, resize_presets
from ..models import ImageRecord

MODES = ("rename", "resize", "convert")
TITLES = {"rename": "Batch rename", "resize": "Batch resize", "convert": "Batch convert"}
LABELS = {"rename": "Apply rename", "resize": "Resize", "convert": "Convert"}
PREVIEW_ROW_LIMIT = 1200          # the Widgets dialogs show the first 1200 rows and validate the whole batch
SOURCE_LIMIT = 20000
_TONE = {"Rename": "g1", "Unchanged": "", "Error": "g4", "Copy": "g1", "Convert": "g2"}

_DEFAULTS = {
    "newName": "", "prefix": "", "suffix": "", "case": RenameCaseMode.KEEP.value, "collapse": False, "seq": True,
    "start": 1, "step": 1, "pad": 3, "sep": "_",
    "preset": "small", "cw": "1920", "ch": "1080", "shrink": False, "ignOri": False, "over": False, "strip": False, "fmt": ".png",
}
_LIMITS = {"start": (0, 999999), "step": (1, 999999), "pad": (0, 8)}


def _int(text, default: int = 0) -> int:
    try:
        return int(float(str(text).replace(",", "").strip()))
    except ValueError:
        return default


class BatchModel(QObject):
    revisionChanged = Signal()
    applied = Signal(str, int)          # mode, how many items the plan would have touched

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None, toasts=None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._toasts = toasts
        self._busy = False
        self._toast_id = 0
        self._done = None
        self._signals = None
        self._exec: tuple = ()           # what Apply would run: ("rename", preview, None) or ("resize" | "convert", plan, options)
        self._memory = memory
        self._mode = "rename"
        self._v: dict[str, object] = dict(_DEFAULTS)
        self._result: dict = {"rows": [], "ok": False, "summary": "", "foot": "", "errors": 0}
        self._stage: str = ""            # --mock: empty files named like the sample photos, so the planners have real paths to look at
        self._revision = 0
        self._active = False
        photos.countsChanged.connect(self._photos_changed)      # the folder finished loading, or a filter changed what is shown

    def _photos_changed(self) -> None:
        if self._active:
            self._refresh()

    # -- the batch ---------------------------------------------------------------------------------------------------------------
    def _file_name(self, row: dict) -> str:
        name = str(row["name"])
        return name if Path(name).suffix else f"{name}.{row['ext']}"

    def _batch(self) -> list[dict]:
        """One entry per shown photo: its thumbnail path, the path the planners look at, its file name and companions."""
        rows = list(self._photos._rows)[:SOURCE_LIMIT]
        out = []
        if self._memory:
            if not self._stage:
                self._stage = tempfile.mkdtemp(prefix="qml-batch-")
            for row in rows:
                name = self._file_name(row)
                path = os.path.join(self._stage, name)
                companions: tuple[str, ...] = ()
                if not os.path.exists(path):
                    Path(path).touch()
                if row["raw"]:
                    jpg = os.path.join(self._stage, Path(name).stem + ".JPG")
                    if not os.path.exists(jpg):
                        Path(jpg).touch()
                    companions = (jpg,)
                out.append({"thumb": row["path"], "path": path, "name": name, "companions": companions})
        else:
            for row in rows:
                out.append({"thumb": row["path"], "path": row["path"], "name": self._file_name(row), "companions": tuple(row.get("companions", ())), "record": row.get("record")})
        return out

    def _compute(self) -> None:
        batch = self._batch()
        v = self._v
        mode = self._mode
        rows: list[dict] = []
        if mode == "rename":
            # a real photo's own size and time: a saved decision is found again by them
            records = [ImageRecord(path=b["path"], name=b["name"], size=b["record"].size if b.get("record") else 0, modified_ns=b["record"].modified_ns if b.get("record") else 0,
                                   companion_paths=b["companions"]) for b in batch]
            rules = BatchRenameRules(
                new_name=str(v["newName"]), prefix=str(v["prefix"]), suffix=str(v["suffix"]), case_mode=RenameCaseMode(v["case"]),
                collapse_whitespace=bool(v["collapse"]), sequence_enabled=len(records) > 1 or bool(v["seq"]),
                sequence_start=int(v["start"]), sequence_step=int(v["step"]), sequence_padding=int(v["pad"]), sequence_separator=str(v["sep"]),
            )
            preview = build_batch_rename_preview(records, rules)
            self._exec = ("rename", preview, None)
            thumbs = {b["path"]: b["thumb"] for b in batch}
            for item in preview.items[:PREVIEW_ROW_LIMIT]:
                pair = ""
                if item.status == "Rename":
                    others = [Path(m.target_path).name for m in item.planned_moves if os.path.normcase(m.source_path) != os.path.normcase(item.record.path)]
                    pair = ", ".join(f"+ {name} follows" for name in others)
                rows.append(self._row(thumbs.get(item.record.path, ""), item.source_name, item.target_name, item.status, item.message, pair))
            summary = f"{preview.renamed_count} rename(s) | {preview.unchanged_count} unchanged | {preview.error_count} error(s)"
            if preview.general_error:
                summary = f"{summary} | {preview.general_error}"
            ok, errors = preview.can_apply, preview.error_count
            foot = (f"Fix {errors} {'error' if errors == 1 else 'errors'} before renaming." if errors
                    else f"The preview is clean. {preview.renamed_count} will be renamed." if preview.renamed_count else "Nothing would change.")
            count = preview.renamed_count
        elif mode == "resize":
            options = ResizeOptions(
                preset_key=str(v["preset"]), custom_width=_int(v["cw"]), custom_height=_int(v["ch"]), shrink_only=bool(v["shrink"]),
                ignore_orientation=bool(v["ignOri"]), overwrite=bool(v["over"]), strip_metadata=bool(v["strip"]),
            )
            plan = build_resize_plan([ResizeSourceItem(b["path"], b["name"]) for b in batch], options)
            self._exec = ("resize", plan, options)
            thumbs = {b["path"]: b["thumb"] for b in batch}
            for item in plan.items[:PREVIEW_ROW_LIMIT]:
                rows.append(self._row(thumbs.get(item.source.source_path, ""), item.source.source_name, item.target_name or item.source.source_name,
                                      item.status, item.message, "", overwrite_tone="g2"))
            errors, ok, count = plan.error_count, plan.can_apply, plan.executable_count
            summary = (f"{plan.error_count} error(s)" if plan.general_error
                       else f"{plan.executable_count} item(s) | {plan.copy_mode} | fits within {plan.target_width} x {plan.target_height}"
                       + (f" | {plan.error_count} error(s)" if plan.error_count else ""))
            foot = (plan.general_error if plan.general_error
                    else f"Fix {errors} {'error' if errors == 1 else 'errors'} before resizing." if errors
                    else f"{count} will be resized as {'overwrites' if v['over'] else 'copies'}.")
        else:
            options = ConvertOptions(output_suffix=str(v["fmt"]), overwrite=bool(v["over"]), strip_metadata=bool(v["strip"]))
            plan = build_convert_plan([ConvertSourceItem(b["path"], b["name"]) for b in batch], options)
            self._exec = ("convert", plan, options)
            thumbs = {b["path"]: b["thumb"] for b in batch}
            for item in plan.items[:PREVIEW_ROW_LIMIT]:
                rows.append(self._row(thumbs.get(item.source.source_path, ""), item.source.source_name, item.target_name or item.source.source_name,
                                      item.status, item.message, "", overwrite_tone="g3"))
            errors, ok, count = plan.error_count, plan.can_apply, plan.executable_count
            summary = (plan.general_error if plan.general_error
                       else f"{plan.executable_count} item(s) | {plan.copy_mode} | {plan.output_label}"
                       + (f" | {plan.error_count} error(s)" if plan.error_count else ""))
            foot = (plan.general_error if plan.general_error
                    else f"Fix {errors} {'error' if errors == 1 else 'errors'} before converting." if errors
                    else f"{count} will be converted.")
        self._result = {"rows": rows, "ok": bool(ok), "summary": summary, "foot": foot, "errors": int(errors), "count": int(count), "total": len(batch)}

    @staticmethod
    def _row(thumb: str, current: str, target: str, status: str, message: str, pair: str, overwrite_tone: str = "g2") -> dict:
        tone = overwrite_tone if status == "Overwrite" else _TONE.get(status, "")
        failed = status == "Error"
        return {"thumb": thumb, "cur": current, "next": target, "status": status, "tone": tone, "err": message if failed else "",
                "note": pair or ("" if failed else message), "same": status == "Unchanged"}

    # -- the form ----------------------------------------------------------------------------------------------------------------
    @Slot(str)
    def begin(self, mode: str) -> None:
        self._active = True
        self._mode = mode if mode in MODES else "rename"
        self._refresh()

    @Slot()
    def end(self) -> None:
        self._active = False

    @Slot(str)
    def setMode(self, mode: str) -> None:
        if mode in MODES and mode != self._mode:
            self._mode = mode
            self._refresh()

    @Slot(str, result="QVariant")
    def get(self, key: str):
        return self._v.get(key, "")

    @Slot(str, "QVariant")
    def set(self, key: str, value) -> None:
        if key not in self._v:
            return
        if key in _LIMITS:
            low, high = _LIMITS[key]
            value = max(low, min(high, _int(value, int(self._v[key]))))
        elif key == "sep":
            value = str(value)[:4]
        elif isinstance(self._v[key], bool):
            value = bool(value)
        else:
            value = str(value)
        if self._v[key] != value:
            self._v[key] = value
            self._refresh()

    @Slot()
    def reset(self) -> None:
        self._v = dict(_DEFAULTS)
        self._refresh()

    @Slot(str, result="QVariantList")
    def options(self, key: str) -> list:
        if key == "case":
            return [{"label": m.value, "value": m.value} for m in RenameCaseMode]
        if key == "preset":
            return [{"label": p.name, "value": p.key} for p in resize_presets()]
        if key == "fmt":
            return [{"label": f.name, "value": f.suffix} for f in convert_formats()]
        return []

    # -- what the window shows ---------------------------------------------------------------------------------------------------
    @Slot(result="QVariantList")
    def rows(self) -> list:
        return self._result["rows"]

    @Slot(str, result=str)
    def scope(self, folder: str) -> str:
        """The line above the table: a bold count, then the rest."""
        total = self._result.get("total", 0)
        if self._mode == "rename":
            return f"{total} image bundles|in {folder}. A RAW file and its JPG companion rename together, and extensions stay as they are."
        return f"{total} images|in {folder}. Copies are saved next to the originals unless Overwrite files is on."

    @Slot(result=str)
    def presetDesc(self) -> str:
        key = str(self._v["preset"])
        return "fits within a custom size" if key == "custom" else preset_for_key(key).description

    @Slot(result=str)
    def fmtDesc(self) -> str:
        return format_for_suffix(str(self._v["fmt"])).description

    @Slot(result=str)
    def note(self) -> str:
        if self._mode == "resize":
            return ("Resize can’t be used on RAW files. Formats that cannot be written directly will export as JPG copies when overwrite is off.")
        return "Convert can’t be used on RAW files. Converted files keep the original dimensions unless you use Resize instead."

    @Slot()
    def apply(self) -> None:
        """Rename, resize or convert what the preview shows. The sample photos of `--mock` have no files: the plan is only counted."""
        count = int(self._result.get("count", 0))
        self.applied.emit(self._mode, count)
        if self._memory or self._busy or not self._exec or not self._result.get("ok") or self._exec[0] != self._mode:
            return
        kind, plan, options = self._exec
        self._busy = True
        self.revisionChanged.emit()
        noun = {"rename": "Renaming", "resize": "Resizing", "convert": "Converting"}[kind]
        self._toast_id = self._toasts.push("info", f"{noun} {count} photo{'s' if count != 1 else ''}\u2026", "Preparing\u2026", seconds=0) if self._toasts is not None else 0
        if kind == "rename":
            from ..batch_rename import BatchRenameApplyTask

            task = BatchRenameApplyTask(plan.planned_moves)
            annotations = {row["path"]: row.get("annotation") for row in self._photos.all_rows()}
            self._done = lambda result: self._rename_done(plan, annotations, result)
        elif kind == "resize":
            from ..image_resize import ResizeApplyTask

            task = ResizeApplyTask(plan, options)
            self._done = lambda result: self._copies_done("Resized", result)
        else:
            from ..image_convert import ConvertApplyTask

            task = ConvertApplyTask(plan, options)
            self._done = lambda result: self._copies_done("Converted", result)
        self._signals = task.signals               # kept: the task is deleted when it has run, and the signals must outlive it
        # bound methods of this object, queued: the results arrive on the UI thread, not on the worker's
        task.signals.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(task)

    def _finished(self, result) -> None:
        done, self._done = self._done, None
        if done is not None:
            done(result)

    # -- the work finishing --------------------------------------------------------------------------------------------------------
    def _progress(self, current: int, total: int, message: str) -> None:
        if self._toasts is not None:
            self._toasts.update(self._toast_id, body=f"{message}  ({current} of {total})")

    def _end(self) -> None:
        self._busy = False
        if self._toasts is not None:
            self._toasts.close(self._toast_id)
        self.revisionChanged.emit()

    def _failed(self, message: str) -> None:
        self._end()
        if self._toasts is not None:
            self._toasts.push("error", "The batch stopped", message)
        self._photos.reload()

    def _copies_done(self, verb: str, outcome) -> None:
        self._end()
        written = len(outcome)
        failed = tuple(getattr(outcome, "failed", ()))
        if self._toasts is not None:
            if failed:
                self._toasts.push("warn", f"{verb} {written}, left out {len(failed)}", f"{Path(failed[0][0]).name}: {failed[0][1]}")
            else:
                self._toasts.push("success", f"{verb} {written} photo{'s' if written != 1 else ''}", "The new files are in the folder.")
        self._photos.reload()

    def _rename_done(self, preview, annotations: dict, moves) -> None:
        from ..record_ops_controller import RecordOpsController
        from .decision_bridge import session_id

        self._end()
        renamed = [item for item in preview.items if item.status == "Rename"]
        updates = []
        for item in renamed:
            annotation = annotations.get(item.record.path)
            if annotation is not None and not annotation.is_empty:
                updates.append((item.record, RecordOpsController.record_after_moves(item.record, item.planned_moves), annotation))
        if updates:
            from ..decision_store import DecisionStore

            DecisionStore().move_annotations(session_id(), [(old.path, new, annotation) for old, new, annotation in updates])
        if self._toasts is not None:
            entry = {"id": self._photos.push_undo(f"rename of {len(renamed)} photos", lambda: self._undo_rename(renamed, updates))}
            self._toasts.push("success", f"Renamed {len(renamed)} photo{'s' if len(renamed) != 1 else ''}", "", "Undo", lambda: self._photos.undo_entry(entry["id"]))
        self._photos.reload()

    def _undo_rename(self, renamed, updates) -> bool:
        from ..file_ops import FileMove, rename_paths
        from .decision_bridge import session_id

        back = tuple(FileMove(source_path=m.target_path, target_path=m.source_path) for item in renamed for m in item.planned_moves)
        try:
            rename_paths(back)
        except Exception as exc:
            if self._toasts is not None:
                self._toasts.push("error", "Could not undo the rename", str(exc))
            return False
        if updates:
            from ..decision_store import DecisionStore

            DecisionStore().move_annotations(session_id(), [(new.path, old, annotation) for old, new, annotation in updates])
        self._photos.reload()
        if self._toasts is not None:
            self._toasts.push("success", f"Renamed {len(renamed)} photo{'s' if len(renamed) != 1 else ''} back")
        return True

    busy = Property(bool, lambda self: self._busy, notify=revisionChanged)
    sampleNote = Property(str, lambda self: "Sample photos: no file will change." if self._memory else "", notify=revisionChanged)

    def _refresh(self) -> None:
        self._compute()
        self._revision += 1
        self.revisionChanged.emit()

    mode = Property(str, lambda self: self._mode, notify=revisionChanged)
    title = Property(str, lambda self: TITLES[self._mode], notify=revisionChanged)
    applyLabel = Property(str, lambda self: LABELS[self._mode], notify=revisionChanged)
    canApply = Property(bool, lambda self: bool(self._result["ok"]), notify=revisionChanged)
    errorCount = Property(int, lambda self: int(self._result["errors"]), notify=revisionChanged)
    summary = Property(str, lambda self: self._result["summary"], notify=revisionChanged)
    foot = Property(str, lambda self: self._result["foot"], notify=revisionChanged)
    revision = Property(int, lambda self: self._revision, notify=revisionChanged)

    def cleanup(self) -> None:
        if self._stage:
            shutil.rmtree(self._stage, ignore_errors=True)
            self._stage = ""

