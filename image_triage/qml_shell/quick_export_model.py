"""Quick Export: pick a preset, pick where, export. The small window the grid's Export…, the viewer's Export button and Ctrl+E open.

It runs the same pipeline as Batch Export (``export_pipeline``) with a preset's settings as they are, so there is nothing to fill in:
Instagram's shapes (cut from the middle of the photo), smaller copies, full-size JPEG, PNG or TIFF, and the person's own presets
from Batch Export. The copies go to an ``Export`` folder beside the photos, or to PocketDrop (written to a folder of the app's own,
then added to PocketDrop's list to send). "More options…" opens Batch Export with the preset filled in.

Planning reads the disk, and a batch can be a whole folder, so the plan is made on the worker thread with the export itself: the window
closes at once and a toast follows the progress. The last preset and destination are remembered.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, Qt, QThreadPool, Signal, Slot

from .. import export_pipeline as pipeline
from ..export_presets import BUILTIN_GROUPS, PresetStore

DESTINATIONS = ("folder", "pocketdrop")
SUBFOLDER = "Export"
DEFAULT_PRESET = "Web 2048 px"
POCKETDROP_KEEP_SECONDS = 2 * 24 * 3600        # copies made for PocketDrop are removed this long after, when a later one is made
SOURCE_LIMIT = 20000
_FORMAT_NAMES = {key: label for key, label, _suffix in pipeline.FORMATS}


def describe(values: dict) -> str:
    """One line for a preset: its size and its format ("1080 × 1350 · JPEG", "Long edge 2048 px · WebP")."""
    v = pipeline.normalized(values)
    mode = v["sizeMode"]
    size = {"original": "Full size", "long": f"Long edge {v['long']} px", "short": f"Short edge {v['short']} px",
            "fit": f"Fits {v['fitW']} × {v['fitH']}", "mp": f"{v['mp']} MP", "pct": f"{v['pct']}%",
            "crop": "Cropped to fit"}[mode]
    fmt = "Same format as the photo" if v["format"] == "original" else _FORMAT_NAMES[v["format"]]
    if v["format"] in ("png", "tif") and v["depth"] == "16":
        fmt += " 16-bit"
    return f"{size} · {fmt}"


def settings_path() -> Path:
    from ..scan_cache import app_data_root

    return app_data_root() / "quick_export.json"


def pocketdrop_root() -> Path:
    return Path(tempfile.gettempdir()) / "Image Triage PocketDrop"


def _new_pocketdrop_folder(now: float | None = None) -> Path:
    """A fresh folder for one export to PocketDrop; earlier ones past ``POCKETDROP_KEEP_SECONDS`` are removed (sent long since)."""
    now = time.time() if now is None else now
    root = pocketdrop_root()
    try:
        for entry in root.iterdir():
            if entry.is_dir() and now - entry.stat().st_mtime > POCKETDROP_KEEP_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-", time.localtime(now)), dir=root))


def _file_name(row: dict) -> str:
    name = str(row["name"])
    return name if Path(name).suffix else f"{name}.{row['ext']}"


class QuickExportTask(pipeline.ExportApplyTask):
    """Plans the export (it looks at the disk) and runs it, both on the worker thread."""

    def __init__(self, sources: list, values: dict) -> None:
        super().__init__(None)
        self.sources = sources
        self.values = values

    def run(self) -> None:
        try:
            self.plan = pipeline.build_plan(self.sources, self.values)
        except Exception as error:  # pragma: no cover - build_plan reports per photo
            self.signals.failed.emit(str(error) or error.__class__.__name__)
            return
        if not self.plan.can_apply:
            self.signals.failed.emit(self.plan.general_error or "Nothing to export.")
            return
        super().run()


class QuickExportModel(QObject):
    changed = Signal()
    sentToPocketDrop = Signal(int)          # how many copies were added: the window shows the PocketDrop panel

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None, toasts=None, pocket_drop=None,
                 store: PresetStore | None = None, settings: Path | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._memory = memory
        self._toasts = toasts
        self._pocket_drop = pocket_drop
        self._store = store or PresetStore()
        self._settings = settings
        self._only = ""
        self._busy = False
        self._toast_id = 0
        self._signals = None
        self._done = None
        saved = self._load()
        self._preset = saved.get("preset", DEFAULT_PRESET)
        self._dest = saved.get("dest", "folder") if saved.get("dest") in DESTINATIONS else "folder"
        photos.selectionChanged.connect(self.changed)
        photos.countsChanged.connect(self.changed)

    # -- remembered choices --------------------------------------------------------------------------------------------------------
    def _settings_file(self) -> Path:
        return self._settings or settings_path()

    def _load(self) -> dict:
        try:
            data = json.loads(self._settings_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        if self._memory:
            return
        try:
            path = self._settings_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"preset": self._preset, "dest": self._dest}), encoding="utf-8")
        except OSError:
            pass

    # -- what the window shows -------------------------------------------------------------------------------------------------------
    def _presets(self) -> list[dict]:
        out = []
        for entry in self._store.presets():
            group = BUILTIN_GROUPS.get(entry["name"], "Full size") if entry["builtin"] else "My presets"
            out.append({"name": entry["name"], "group": group, "desc": describe(entry["values"])})
        return out

    def _current(self) -> str:
        """The chosen preset, or the default when it was deleted since."""
        names = [p["name"] for p in self._store.presets()]
        if self._preset in names:
            return self._preset
        return DEFAULT_PRESET if DEFAULT_PRESET in names else (names[0] if names else "")

    def _rows(self) -> list[dict]:
        if self._only:
            key = os.path.normcase(os.path.abspath(self._only))
            return [r for r in self._photos.all_rows() if os.path.normcase(os.path.abspath(r["path"])) == key][:1]
        return self._photos.scope_rows()[:SOURCE_LIMIT]

    presets = Property("QVariantList", _presets, notify=changed)
    preset = Property(str, _current, notify=changed)
    dest = Property(str, lambda self: self._dest, notify=changed)
    busy = Property(bool, lambda self: self._busy, notify=changed)
    only = Property(str, lambda self: self._only, notify=changed)
    count = Property(int, lambda self: len(self._rows()), notify=changed)

    @Slot(result=str)
    def summary(self) -> str:
        """The line above the Export button: how many photos, and where their copies go."""
        count = len(self._rows())
        photos = "This photo" if self._only else f"{count} photo{'s' if count != 1 else ''}"
        if not count:
            return "No photos to export."
        if self._dest == "pocketdrop":
            return f"{photos} → PocketDrop, ready to send"
        rows = self._rows()
        folders = {os.path.dirname(r["path"]) for r in rows}
        where = f"{Path(next(iter(folders))).name}{os.sep}{SUBFOLDER}" if len(folders) == 1 else f"an {SUBFOLDER} folder beside each"
        return f"{photos} → {where}"

    # -- the form ----------------------------------------------------------------------------------------------------------------------
    @Slot(str)
    def begin(self, only: str) -> None:
        """Open for the photo at ``only`` ("" for the usual scope: the picked photos, else every photo shown)."""
        self._only = only or ""
        self.changed.emit()

    @Slot(str)
    def choose(self, name: str) -> None:
        if name != self._preset and self._store.get(name) is not None:
            self._preset = name
            self._save()
            self.changed.emit()

    @Slot(str)
    def setDest(self, dest: str) -> None:
        if dest in DESTINATIONS and dest != self._dest:
            self._dest = dest
            self._save()
            self.changed.emit()

    # -- exporting ---------------------------------------------------------------------------------------------------------------------
    def values(self, folder: Path | None = None) -> dict:
        """The pipeline's settings for the chosen preset and destination."""
        values = {**pipeline.DEFAULTS, **(self._store.get(self._current()) or {})}
        values["exists"] = "unique"                       # a quick export never writes over a file
        values["xPrefix"] = values["xSuffix"] = ""
        if folder is not None:
            values.update(dest="choose", folder=str(folder))
        else:
            values.update(dest="subfolder", sub=SUBFOLDER)
        return pipeline.normalized(values)

    @Slot(result=bool)
    def run(self) -> bool:
        """Export the photos; False when there is nothing to export or an export is already running."""
        rows = self._rows()
        if self._busy or not rows:
            return False
        count = len(rows)
        noun = f"{count} photo{'s' if count != 1 else ''}"
        if self._memory:                                   # --mock: the sample photos have no files
            if self._toasts is not None:
                self._toasts.push("success", f"Exported {noun}", f"{self._current()} (sample photos: nothing was written).")
            return True
        to_pocket = self._dest == "pocketdrop"
        folder = _new_pocketdrop_folder() if to_pocket else None
        sources = [pipeline.ExportSource(r["path"], _file_name(r)) for r in rows]
        task = QuickExportTask(sources, self.values(folder))
        self._busy = True
        self.changed.emit()
        if self._toasts is not None:
            self._toast_id = self._toasts.push("info", f"Exporting {noun}…", self._current(), seconds=0)
        self._done = (lambda outcome: self._sent(outcome)) if to_pocket else (lambda outcome: self._exported(outcome))
        self._signals = task.signals                      # kept: the task is deleted when it has run, and its signals must outlive it
        task.signals.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(task)
        return True

    def _progress(self, current: int, total: int, message: str) -> None:
        if self._toasts is not None:
            self._toasts.update(self._toast_id, body=f"{message}  ({current} of {total})")

    def _end(self) -> None:
        self._busy = False
        if self._toasts is not None:
            self._toasts.close(self._toast_id)
        self.changed.emit()

    def _finished(self, outcome) -> None:
        done, self._done = self._done, None
        if done is not None:
            done(outcome)

    def _failed(self, message: str) -> None:
        self._end()
        if self._toasts is not None:
            self._toasts.push("error", "The export stopped", message)

    def _left_out(self, outcome) -> tuple:
        return tuple(getattr(outcome, "failed", ()))

    def _exported(self, outcome) -> None:
        from .batch_model import _show_in_folder

        self._end()
        written, failed = list(outcome), self._left_out(outcome)
        if self._toasts is not None:
            where = str(Path(written[0]).parent) if written else ""
            show = (lambda: _show_in_folder(written[0] if len(written) == 1 else where)) if where else None
            if failed:
                self._toasts.push("warn", f"Exported {len(written)}, left out {len(failed)}", f"{Path(failed[0][0]).name}: {failed[0][1]}",
                                  "Show" if show else "", show, seconds=12.0)
            else:
                self._toasts.push("success", f"Exported {len(written)} photo{'s' if len(written) != 1 else ''}",
                                  f"The files are in {where}." if where else "", "Show" if show else "", show, seconds=10.0)
        self._photos.reload()                              # the Export folder is new, or has new files

    def _sent(self, outcome) -> None:
        self._end()
        written, failed = list(outcome), self._left_out(outcome)
        if written and self._pocket_drop is not None:
            self._pocket_drop.addPaths(written)
        if self._toasts is not None:
            title = f"{len(written)} photo{'s' if len(written) != 1 else ''} ready in PocketDrop"
            body = f"Left out {len(failed)}: {Path(failed[0][0]).name}: {failed[0][1]}" if failed else "Send them from the PocketDrop panel."
            self._toasts.push("warn" if failed else "success", title, body, seconds=10.0)
        if written:
            self.sentToPocketDrop.emit(len(written))
