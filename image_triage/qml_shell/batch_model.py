"""Batch rename and export for the QML shell.

Export (``export_pipeline``) replaced the separate resize and convert tools: it sizes, converts the colour profile, encodes, filters
the metadata and names the files in one pass, with export presets (``export_presets``) and a result preview of the selected photo.
The old ``resize`` and ``convert`` commands open Export with the size step (or the original size) filled in.


Planning a batch looks at the disk for every photo (does the name exist, may it be used). A small batch is planned at once; a
large one (more than ``SYNC_LIMIT`` photos: a whole folder or library can be tens of thousands) is planned on a worker thread a moment
after the last change, so typing in a field or switching tabs never waits for it, and only the newest plan is kept. While it is being
planned the summary says so and Apply waits.

Rename uses the shared planner (`batch_rename.build_batch_rename_preview`). The scope is every photo the grid shows. **Applying is
real** (not for the sample photos of `--mock`), on a worker thread with a toast that follows the progress. A rename moves the saved
decisions with the files and can be undone (Ctrl+Z); an export writes new files and leaves the originals alone.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Property, QObject, Qt, QThreadPool, QTimer, Signal, Slot

from ..batch_rename import BatchRenameRules, RenameCaseMode, build_batch_rename_preview
import threading
from collections import OrderedDict

from .. import export_pipeline as pipeline
from ..export_presets import PresetStore
from ..models import ImageRecord

MODES = ("rename", "export")
ALIASES = {"resize": "export", "convert": "export"}      # the old tools' commands open Export
TITLES = {"rename": "Batch rename", "export": "Batch export"}
LABELS = {"rename": "Apply rename", "export": "Export"}
PREVIEW_ROW_LIMIT = 1200          # the Widgets dialogs show the first 1200 rows and validate the whole batch
SOURCE_LIMIT = 20000
SYNC_LIMIT = 300                  # a batch this small is planned at once; a larger one on a worker thread
CHECK_DELAY_MS = 150              # a burst of changes (typing) makes one plan, this long after the last of them
_TONE = {"Rename": "g1", "Unchanged": "", "Error": "g4", "Copy": "g1", "Convert": "g2", "Export": "g1", "Skip": ""}

_DEFAULTS = {
    "newName": "", "prefix": "", "suffix": "", "case": RenameCaseMode.KEEP.value, "collapse": False, "seq": True,
    "start": 1, "step": 1, "pad": 3, "sep": "_",
    **pipeline.DEFAULTS, "xPreset": "",
}
_EXPORT_KEYS = tuple(pipeline.DEFAULTS)
_KEPT_SOURCES = 3
_LIMITS = {"start": (0, 999999), "step": (1, 999999), "pad": (0, 8)}


def _int(text, default: int = 0) -> int:
    try:
        return int(float(str(text).replace(",", "").strip()))
    except ValueError:
        return default


class BatchModel(QObject):
    revisionChanged = Signal()
    previewChanged = Signal()
    applied = Signal(str, int)          # mode, how many items the plan would have touched
    _previewReady = Signal(int, object, str)
    _planReady = Signal(int, object)

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None, toasts=None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._toasts = toasts
        self._busy = False
        self._toast_id = 0
        self._done = None
        self._signals = None
        self._exec: tuple = ()           # what Apply would run: ("rename", preview, None) or ("export", plan, None)
        self._memory = memory
        self._mode = "rename"
        self._v: dict[str, object] = dict(_DEFAULTS)
        self._result: dict = {"rows": [], "ok": False, "summary": "", "foot": "", "errors": 0}
        self._stage: str = ""            # --mock: empty files named like the sample photos, so the planners have real paths to look at
        self._revision = 0
        self._active = False
        self._store = PresetStore()
        self._batch_cache: list[dict] = []
        self._pindex = 0
        self._pgen = 0
        self._pbusy = False
        self._before = self._after = self._detail = ""
        self._pinfo = self._perror = ""
        self._sources: OrderedDict = OrderedDict()   # path -> (image, exif, icc), the last few photos previewed
        self._sources_lock = threading.Lock()
        self._previewReady.connect(self._preview_arrived)
        self._cgen = 0                   # which plan is the newest asked for: an older one arriving late is dropped
        self._checking = False
        self._result_mode = ""
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(CHECK_DELAY_MS)
        self._check_timer.timeout.connect(self._start_check)
        self._planReady.connect(self._plan_arrived)
        photos.selectionChanged.connect(self._photos_changed)
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
        rows = self._photos.scope_rows()[:SOURCE_LIMIT]
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

    @classmethod
    def _plan(cls, batch: list[dict], v: dict, mode: str) -> tuple[dict, tuple]:
        """``(result, exec)`` for ``batch`` with the settings ``v`` in ``mode``: what the window shows and what Apply would run. Looks at
        the disk and touches nothing of the model, so it can run on a worker thread."""
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
            plan_exec = ("rename", preview, None)
            thumbs = {b["path"]: b["thumb"] for b in batch}
            for item in preview.items[:PREVIEW_ROW_LIMIT]:
                pair = ""
                if item.status == "Rename":
                    others = [Path(m.target_path).name for m in item.planned_moves if os.path.normcase(m.source_path) != os.path.normcase(item.record.path)]
                    pair = ", ".join(f"+ {name} follows" for name in others)
                rows.append(cls._row(thumbs.get(item.record.path, ""), item.source_name, item.target_name, item.status, item.message, pair))
            summary = f"{preview.renamed_count} rename(s) | {preview.unchanged_count} unchanged | {preview.error_count} error(s)"
            if preview.general_error:
                summary = f"{summary} | {preview.general_error}"
            ok, errors = preview.can_apply, preview.error_count
            foot = (f"Fix {errors} {'error' if errors == 1 else 'errors'} before renaming." if errors
                    else f"The preview is clean. {preview.renamed_count} will be renamed." if preview.renamed_count else "Nothing would change.")
            count = preview.renamed_count
        else:
            plan = pipeline.build_plan([pipeline.ExportSource(b["path"], b["name"]) for b in batch], {k: v[k] for k in _EXPORT_KEYS})
            plan_exec = ("export", plan, None)
            thumbs = {b["path"]: b["thumb"] for b in batch}
            for item in plan.items[:PREVIEW_ROW_LIMIT]:
                where = str(Path(item.target_path).parent) if item.target_path else ""
                rows.append(cls._row(thumbs.get(item.source.path, ""), item.source.name, item.target_name, item.status,
                                      item.message, "" if item.status == "Error" else where, overwrite_tone="g3"))
            errors, ok, count = plan.error_count, plan.can_apply, len(plan.executable)
            fmt = next(label for key, label, _ in pipeline.FORMATS if key == plan.values["format"])
            fmt = "Each photo's own format" if plan.values["format"] == "original" else fmt
            if plan.values["profile"] == "file" and plan.values["profileFile"]:
                from ..soft_proof import profile_name

                profile = profile_name(plan.values["profileFile"])
            else:
                profile = next(label for key, label in pipeline.PROFILES if key == plan.values["profile"])
            summary = (plan.general_error if plan.general_error
                       else f"{count} file(s) | {fmt} | {profile}" + (f" | {plan.skip_count} skipped" if plan.skip_count else "")
                       + (f" | {errors} error(s)" if errors else ""))
            foot = (plan.general_error if plan.general_error
                    else f"Fix {errors} {'error' if errors == 1 else 'errors'} before exporting." if errors
                    else f"{count} will be exported." if count else "Nothing to export.")
        result = {"rows": rows, "ok": bool(ok), "summary": summary, "foot": foot, "errors": int(errors), "count": int(count), "total": len(batch)}
        return result, plan_exec

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
        self._mode = self._mode_for(mode)
        self._pindex = 0
        self._refresh()

    @Slot()
    def end(self) -> None:
        self._active = False
        self._pgen += 1
        with self._sources_lock:
            self._sources.clear()

    @Slot(str)
    def setMode(self, mode: str) -> None:
        mode = self._mode_for(mode)
        if mode != self._mode:
            self._mode = mode
            self._refresh()

    def _mode_for(self, mode: str) -> str:
        """The mode to show for a command: resize opens Export with a long edge to fill in, convert with the original size."""
        if mode == "resize" and self._v["sizeMode"] == "original":
            self._v["sizeMode"] = "long"
        elif mode == "convert":
            self._v["sizeMode"] = "original"
        mode = ALIASES.get(mode, mode)
        return mode if mode in MODES else "rename"

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
            if key in _EXPORT_KEYS:
                self._v["xPreset"] = ""        # the settings no longer match the preset they came from
            self._refresh()

    @Slot()
    def reset(self) -> None:
        self._v = dict(_DEFAULTS)
        self._refresh()

    @Slot(str, result="QVariantList")
    def options(self, key: str) -> list:
        if key == "case":
            return [{"label": m.value, "value": m.value} for m in RenameCaseMode]
        lists = {"sizeMode": pipeline.SIZE_MODES, "profile": pipeline.PROFILES, "intent": pipeline.INTENTS, "meta": pipeline.METADATA,
                 "dest": pipeline.DESTINATIONS, "exists": pipeline.EXISTS, "depth": (("8", "8 bits per channel"), ("16", "16 bits per channel")),
                 "sharpen": pipeline.SHARPEN, "sharpenAmount": pipeline.SHARPEN_AMOUNTS, "wm": pipeline.WATERMARKS, "wmPos": pipeline.POSITIONS,
                 "wmColor": pipeline.WM_COLORS}
        if key in lists:
            return [{"label": label, "value": value} for value, label in lists[key]]
        if key == "format":
            return [{"label": label, "value": value} for value, label, _ in pipeline.FORMATS]
        if key == "xPreset":
            return [{"label": "Custom", "value": ""}] + [{"label": p["name"], "value": p["name"]} for p in self._store.presets()]
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
        return f"{total} photos|in {folder}. Photos with edits are exported with them, at full size; the originals are not changed."

    @Slot(str)
    def usePreset(self, name: str) -> None:
        """Fill the export settings from a preset (a chosen folder stays as it is)."""
        values = self._store.get(name)
        if values is None:
            return
        folder = self._v["folder"]
        self._v.update(values)
        if self._v["dest"] == "choose":
            self._v["folder"] = folder
        self._v["xPreset"] = name
        self._refresh()

    @Slot(str, result=str)
    def savePreset(self, name: str) -> str:
        """Save the current export settings as a preset; returns why it could not be, or ""."""
        problem = self._store.save(name, {k: self._v[k] for k in _EXPORT_KEYS})
        if not problem:
            self._v["xPreset"] = name.strip()
            self._refresh()
        return problem

    @Slot(str)
    def deletePreset(self, name: str) -> None:
        if self._store.delete(name):
            if self._v["xPreset"] == name:
                self._v["xPreset"] = ""
            self._refresh()

    @Slot(str, result=bool)
    def presetIsBuiltin(self, name: str) -> bool:
        return any(p["name"] == name and p["builtin"] for p in self._store.presets())

    @Slot(result=str)
    def sizeDesc(self) -> str:
        v = pipeline.normalized({k: self._v[k] for k in _EXPORT_KEYS})
        mode = v["sizeMode"]
        if mode == "original":
            return "The photo's own size."
        enlarge = "" if v["noEnlarge"] else " Smaller photos are enlarged."
        return {"long": "The longer side becomes this many pixels.", "short": "The shorter side becomes this many pixels.",
                "fit": "Fits inside this box, keeping its shape.", "mp": "About this many million pixels.",
                "pct": "This share of the photo's size."}[mode] + enlarge

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
        noun = {"rename": "Renaming", "export": "Exporting"}[kind]
        self._toast_id = self._toasts.push("info", f"{noun} {count} photo{'s' if count != 1 else ''}\u2026", "Preparing\u2026", seconds=0) if self._toasts is not None else 0
        if kind == "rename":
            from ..batch_rename import BatchRenameApplyTask

            task = BatchRenameApplyTask(plan.planned_moves)
            annotations = {row["path"]: row.get("annotation") for row in self._photos.all_rows()}
            self._done = lambda result: self._rename_done(plan, annotations, result)
        else:
            task = pipeline.ExportApplyTask(plan)
            self._done = lambda result: self._copies_done("Exported", result)
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
                where = str(Path(outcome[0]).parent) if written else ""
                self._toasts.push("success", f"{verb} {written} photo{'s' if written != 1 else ''}", f"The files are in {where}." if where else "")
        self._photos.reload()

    def _rename_done(self, preview, annotations: dict, moves) -> None:
        from ..file_ops import record_after_moves
        from .decision_bridge import session_id

        self._end()
        renamed = [item for item in preview.items if item.status == "Rename"]
        updates = []
        for item in renamed:
            annotation = annotations.get(item.record.path)
            if annotation is not None and not annotation.is_empty:
                updates.append((item.record, record_after_moves(item.record, item.planned_moves), annotation))
        if updates:
            from ..decision_store import DecisionStore

            DecisionStore().move_annotations(session_id(), [(old.path, new, annotation) for old, new, annotation in updates])
        from ..edit_carry import carry_edits

        left = carry_edits([(m.source_path, m.target_path) for item in renamed for m in item.planned_moves])   # the edits are named after the photo
        if left and self._toasts is not None:
            self._toasts.push("warn", "Some edits did not follow the rename", ", ".join(left[:3]))
        from .file_actions import rekey_collections

        # collections hold a photo by its path, which has just changed
        if not rekey_collections([(m.source_path, m.target_path) for item in renamed for m in item.planned_moves]) and self._toasts is not None:
            self._toasts.push("warn", "Collections were not updated", "The photos were renamed, but the collections they are in still hold the old names.")
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
        from ..edit_carry import carry_edits

        carry_edits([(m.source_path, m.target_path) for m in back])
        from .file_actions import rekey_collections

        rekey_collections([(m.source_path, m.target_path) for m in back])
        if updates:
            from ..decision_store import DecisionStore

            DecisionStore().move_annotations(session_id(), [(new.path, old, annotation) for old, new, annotation in updates])
        self._photos.reload()
        if self._toasts is not None:
            self._toasts.push("success", f"Renamed {len(renamed)} photo{'s' if len(renamed) != 1 else ''} back")
        return True

    busy = Property(bool, lambda self: self._busy, notify=revisionChanged)
    checking = Property(bool, lambda self: self._checking, notify=revisionChanged)
    sampleNote = Property(str, lambda self: "Sample photos: no file will change." if self._memory else "", notify=revisionChanged)

    def _refresh(self) -> None:
        batch = self._batch_cache = self._batch()
        self._cgen += 1
        if len(batch) <= SYNC_LIMIT:
            self._check_timer.stop()
            self._result, self._exec = self._plan(batch, dict(self._v), self._mode)
            self._result_mode, self._checking = self._mode, False
        else:
            # Planned on a worker thread shortly: until then nothing can be applied, and another mode's rows are not shown.
            rows = self._result["rows"] if self._result_mode == self._mode else []
            self._result = {**self._result, "rows": rows, "ok": False, "summary": f"Checking {len(batch)} photos\u2026",
                            "foot": "Checking the names and folders\u2026", "errors": 0}
            self._exec, self._checking = (), True
            self._check_timer.start()
        self._revision += 1
        self.revisionChanged.emit()
        self._render_preview()

    def _start_check(self) -> None:
        generation, batch, values, mode = self._cgen, list(self._batch_cache), dict(self._v), self._mode

        def work():
            try:
                planned = self._plan(batch, values, mode)
            except Exception as exc:          # a disk that went away: say so instead of leaving the window checking
                message = str(exc) or exc.__class__.__name__
                planned = ({"rows": [], "ok": False, "summary": message, "foot": message, "errors": 1, "count": 0, "total": len(batch)}, ())
            try:
                self._planReady.emit(generation, (mode, planned))
            except RuntimeError:
                pass                            # the window was closed meanwhile

        threading.Thread(target=work, name="batch-plan", daemon=True).start()

    def _plan_arrived(self, generation: int, payload) -> None:
        if generation != self._cgen:
            return                                # settings changed since: a newer plan is on its way
        mode, (result, plan_exec) = payload
        self._result, self._exec, self._result_mode, self._checking = result, plan_exec, mode, False
        self._revision += 1
        self.revisionChanged.emit()

    # -- the result preview --------------------------------------------------------------------------------------------------------
    def _preview_entry(self) -> dict | None:
        batch = self._batch_cache
        if not batch:
            return None
        self._pindex = max(0, min(self._pindex, len(batch) - 1))
        return batch[self._pindex]

    def _render_preview(self) -> None:
        self._pgen += 1
        entry = self._preview_entry()
        if entry is None or not self._active:
            self._before = self._after = self._detail = self._pinfo = self._perror = ""
            self._pbusy = False
            self.previewChanged.emit()
            return
        generation, values, mode = self._pgen, {k: self._v[k] for k in _EXPORT_KEYS}, self._mode
        path = entry["thumb"] if self._memory else entry["path"]     # the sample photos' staged files are empty
        self._pbusy = True
        self.previewChanged.emit()

        def work():
            result, error = None, ""
            try:
                with self._sources_lock:
                    kept = self._sources.get(path)
                if kept is None:
                    kept = pipeline.load_source(path)
                    with self._sources_lock:
                        self._sources[path] = kept
                        while len(self._sources) > _KEPT_SOURCES:
                            self._sources.popitem(last=False)
                if generation == self._pgen:
                    image, _exif, icc = kept
                    result = pipeline.make_preview(image, icc, values if mode == "export" else pipeline.DEFAULTS)
            except pipeline.EditedRenderFailed as exc:
                error = f"Its edits could not be rendered: {exc.reason}"
            except Exception as exc:
                error = str(exc) or "The photo could not be read"
            try:
                self._previewReady.emit(generation, result, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="batch-preview", daemon=True).start()

    def _preview_arrived(self, generation: int, result, error: str) -> None:
        if generation != self._pgen:
            return
        from .raw_preview import put_thumbnail

        self._pbusy = False
        self._perror = error
        if result is None:
            self._before = self._after = self._detail = self._pinfo = ""
        else:
            self._before = put_thumbnail(f"batch/{generation}/before", result.before)
            self._after = put_thumbnail(f"batch/{generation}/after", result.after)
            self._detail = put_thumbnail(f"batch/{generation}/detail", result.detail) if result.detail is not None else ""
            self._pinfo = (f"{result.width} x {result.height} px  ·  about {pipeline.human_bytes(result.estimated_bytes)}"
                           if self._mode == "export" else "")
        self.previewChanged.emit()

    @Slot(int)
    def setPreviewIndex(self, index: int) -> None:
        if self._batch_cache and index != self._pindex:
            self._pindex = max(0, min(index, len(self._batch_cache) - 1))
            self._render_preview()

    @Slot(int)
    def stepPreview(self, delta: int) -> None:
        self.setPreviewIndex(self._pindex + delta)

    previewIndex = Property(int, lambda self: self._pindex, notify=previewChanged)
    previewBefore = Property(str, lambda self: self._before, notify=previewChanged)
    previewAfter = Property(str, lambda self: self._after, notify=previewChanged)
    previewDetail = Property(str, lambda self: self._detail, notify=previewChanged)
    previewInfo = Property(str, lambda self: self._pinfo, notify=previewChanged)
    previewError = Property(str, lambda self: self._perror, notify=previewChanged)
    previewBusy = Property(bool, lambda self: self._pbusy, notify=previewChanged)
    previewName = Property(str, lambda self: (self._preview_entry() or {}).get("name", ""), notify=previewChanged)
    previewCount = Property(int, lambda self: len(self._batch_cache), notify=previewChanged)

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
