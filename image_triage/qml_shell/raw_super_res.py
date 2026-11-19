"""Detail > Super Resolution, seen in the RAW editor.

The editor shows the whole photo upscaled at once, as an export makes it: with the box ticked the photo counts as twice its size
(``RawEditor.fullWidth``), so 100% is one upscaled pixel per screen pixel, and once the full-resolution picture is developed the whole
of it is upscaled by the same model in one run (``camera_raw_super_resolution``'s model), under the AI working overlay with its
progress. Nothing is laid over the picture until the run is done; then all of it is.

A 2x 45 MP photo is 180 MP: too large for one texture (16384 px a side) and ~730 MB in memory. So the upscale worker writes the result
straight from its array as JPEG tiles in a temporary folder (``UpscaleRequest.tiles_dir``), and the viewer loads the tiles on screen
from there (``patches``). An edit makes a new full-resolution picture, and with it a new run; its tiles replace the old ones.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot

from . import raw_preview

SETTLE_MS = 300           # edits settle this long before a new run is started
TILE = 1024               # pixels of the upscaled photo per tile side (the worker writes them)
SCALE = 2
MODEL = "realesrgan-x2plus"


def tiles_for(view, width, height, grow=0):
    """The tiles ``(ix, iy)`` covering ``view`` (fractions) of an upscaled ``width`` x ``height`` photo, ``grow`` tiles wider each side."""
    left, top, right, bottom = view
    cols, rows = -(-width // TILE), -(-height // TILE)
    x0 = max(0, int(left * width) // TILE - grow)
    y0 = max(0, int(top * height) // TILE - grow)
    x1 = min(cols - 1, max(0, int(right * width - 1)) // TILE + grow)
    y1 = min(rows - 1, max(0, int(bottom * height - 1)) // TILE + grow)
    return [(ix, iy) for iy in range(y0, y1 + 1) for ix in range(x0, x1 + 1)]


def _upscale_whole(image, folder: Path, cancel, progress):
    """``image`` (QImage, the whole photo) upscaled by the model, written as tiles into ``folder``. Returns the upscaled size."""
    from PySide6.QtGui import QImage

    from .. import upscale_service as service

    state = service.check_availability()
    if not state.runtime_ok:
        raise RuntimeError(state.runtime_reason or "Super Resolution needs the AI runtime (AI > AI Center).")
    if state.models.get(MODEL):
        raise RuntimeError("The Super Resolution model is not installed. Install it in AI > AI Center.")
    source = folder.parent / "photo.png"
    if not image.convertToFormat(QImage.Format.Format_RGB888).save(str(source), "PNG", 100):
        raise RuntimeError("The photo to upscale could not be written")
    try:
        request = service.UpscaleRequest(input_path=source, model_key=MODEL, output_path=folder.parent / "unused.png",
                                         apply_orientation=False, keep_metadata=False, tiles_dir=folder, tile=TILE)
        result = service.run_upscale(request, progress, cancel)
    finally:
        source.unlink(missing_ok=True)
    return tuple(result.output_size)


class RawSuperRes(QObject):
    changed = Signal()
    _done = Signal(int, object, str)
    _step = Signal(int, str)

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._view = None                # (l, t, r, b) of the picture in view while zoomed, else None
        self._source = ""                # the full-resolution picture the tiles are of (or being made of)
        self._folder = None              # the folder the run wrote the tiles into (inside its own temporary folder)
        self._size = None                # the upscaled size, once the run is done
        self._gen = 0
        self._running = False
        self._cancel = threading.Event()
        self._paused = ""                # the picture Cancel was pressed for: no new run until the picture changes
        self._busy = False
        self._message = self._error = ""
        self.upscale = _upscale_whole    # tests replace it
        self._timer = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._timer.timeout.connect(self._check)
        self._done.connect(self._arrived)
        self._step.connect(self._report)
        raw.changed.connect(self._timer.start)

    busy = Property(bool, lambda self: self._busy, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)

    @Property(str, notify=changed)
    def status(self):
        """The line under the checkbox: what Super Resolution is doing, or how to see it (never that it is done when it is not)."""
        if self._error:
            return self._error
        if self._message:
            return self._message
        size = f"{self.raw.property('fullWidth')} × {self.raw.property('fullHeight')}"
        if self._size is not None:
            return f"Applied to the whole photo ({size}). Zoom in to 100% or closer to see it."
        return f"Zoom in to 100% or closer to upscale the photo ({size}), as an export does."

    @Property("QVariantList", notify=changed)
    def patches(self):
        """The upscaled tiles in and next to the view (fractions of the photo), once the whole photo is done."""
        if self._size is None or self._view is None or self._folder is None:
            return []
        w, h = self._size
        out = []
        for ix, iy in tiles_for(self._view, w, h, grow=1):
            x0, y0 = ix * TILE, iy * TILE
            x1, y1 = min(w, x0 + TILE), min(h, y0 + TILE)
            path = self._folder / f"{ix}_{iy}.jpg"
            out.append({"url": QUrl.fromLocalFile(str(path)).toString(), "l": x0 / w, "t": y0 / h, "r": x1 / w, "b": y1 / h})
        return out

    patch = Property("QVariantMap", lambda self: (self.patches[0] if self.patches else {}), notify=changed)

    @Slot(float, float, float, float, bool)
    def setView(self, left, top, right, bottom, zoomed):
        self._view = (left, top, right, bottom) if zoomed and right > left and bottom > top else None
        self._timer.start()
        self.changed.emit()

    @Slot()
    def cancel(self):
        """Stop the run in progress (the overlay's Cancel); no new run until the picture changes (an edit)."""
        if self._running:
            self._cancel.set()
            self._gen += 1
            self._running = False
        self._paused = self._source
        self._busy, self._message = False, ""
        self.changed.emit()

    def _drop(self):
        """Forget the tiles (and stop a run): their folder is deleted."""
        if self._running:
            self._cancel.set()
            self._gen += 1
            self._running = False
        if self._folder is not None:
            shutil.rmtree(self._folder.parent, ignore_errors=True)
        self._folder, self._size, self._source = None, None, ""

    def shutdown(self):
        self._drop()

    def _check(self):
        raw = self.raw
        if raw._draft.get("superResolution") is not True or not raw.loaded or raw.saving:
            if self._folder is not None or self._running:
                self._drop()
            self._error, self._busy, self._message = "", False, ""
            self.changed.emit()
            return
        if self._running:
            self.changed.emit()
            return
        if not raw._detail_mode or not raw._detail:
            if self._view is not None and raw._detail_mode:
                self._busy, self._message = True, "Preparing the full-resolution picture…"   # developed first, then upscaled
            else:
                self._busy, self._message = False, ""
            self.changed.emit()
            return
        source = raw.property("preview")
        if source == self._source and (self._size is not None or self._error):
            self._busy, self._message = False, ""
            self.changed.emit()
            return
        if source == self._paused:
            self._busy, self._message = False, ""
            self.changed.emit()
            return
        self._drop()                               # an edit made a new picture: its tiles are of the old one
        self._start(source)

    def _start(self, source):
        frame = raw_preview.displayed_frame(source, proof=False)
        if frame.isNull():
            return
        self._gen += 1
        generation, cancel = self._gen, threading.Event()
        self._cancel, self._running, self._source, self._error, self._paused = cancel, True, source, "", ""
        scratch = Path(tempfile.mkdtemp(prefix="image-triage-sr-"))
        folder = scratch / "tiles"
        self._busy, self._message = True, "Upscaling the whole photo…"
        self.changed.emit()
        upscale = self.upscale

        def progress(update):
            fraction = getattr(update, "fraction", None)
            if fraction is not None and not cancel.is_set():
                try:
                    self._step.emit(generation, f"Upscaling the whole photo… {int(round(float(fraction) * 100))}%")
                except RuntimeError:
                    pass

        def work():
            size, error = None, ""
            try:
                size = tuple(upscale(frame, folder, cancel, progress))
            except Exception as exc:
                error = "Super Resolution was cancelled" if cancel.is_set() else (str(exc) or "Super Resolution failed")
            if cancel.is_set():
                shutil.rmtree(scratch, ignore_errors=True)
                return
            try:
                self._done.emit(generation, (scratch, size), error)
            except RuntimeError:
                shutil.rmtree(scratch, ignore_errors=True)           # the editor was closed meanwhile

        threading.Thread(target=work, name="qml-super-resolution", daemon=True).start()

    def _report(self, generation, text):
        if self._running and generation == self._gen:
            self._message = text
            self.changed.emit()

    def _arrived(self, generation, result, error):
        scratch, size = result
        if generation != self._gen:
            shutil.rmtree(scratch, ignore_errors=True)
            return
        self._running = False
        self._busy, self._message = False, ""
        if error:
            shutil.rmtree(scratch, ignore_errors=True)
            self._error = error
        else:
            self._folder, self._size, self._error = scratch / "tiles", size, ""
        self.changed.emit()
        self._timer.start()                        # an edit made while it ran wants a new run
