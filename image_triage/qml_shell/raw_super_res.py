"""Detail > Super Resolution, seen in the RAW editor.

Super Resolution itself is applied at output: the export upsizes the whole finished photo 2x (``camera_raw_super_resolution``), which
takes the better part of a minute for a 45 MP raw and gives a picture far too large to keep on screen. The editor shows what it does
where it can be seen, at 100% and closer: with the box ticked the photo counts as twice its size (``RawEditor.fullWidth``), so 100% is
one upscaled pixel per screen pixel, and the photo is upscaled by the same model in tiles laid over the picture.

The tiles are a fixed grid (``TILE`` pixels of the full-resolution picture each). The ones in view that are missing are upscaled
together first, under the AI working overlay; then a ring of tiles around the view in the background, so a small pan needs nothing.
Tiles are kept (up to ``BUDGET_BYTES``, the least recently seen go first), so going back to a part already seen is instant. An edit
makes a new full-resolution picture, and with it new tiles. Before any of this the full-resolution picture itself has to be developed
(about 10 s for 45 MP); that wait is shown as work too.
"""
from __future__ import annotations

import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from . import raw_preview

SETTLE_MS = 250           # a pan or zoom settles this long before tiles are asked for
TILE = 512                # pixels of the full-resolution picture per tile side
OVERLAP = 16              # pixels of context each side of a job, cut away after upscaling, so tiles meet without seams
MAX_JOB_TILES = 16        # tiles upscaled in one run of the model (a run has ~3 s of start-up, so tiles are batched)
BUDGET_BYTES = 384 * 1024 * 1024
MODEL = "realesrgan-x2plus"


def tiles_for(view, width, height, grow=0):
    """The grid tiles ``(ix, iy)`` covering ``view`` (fractions) of a ``width`` x ``height`` picture, ``grow`` tiles wider each side."""
    left, top, right, bottom = view
    cols, rows = -(-width // TILE), -(-height // TILE)
    x0 = max(0, int(left * width) // TILE - grow)
    y0 = max(0, int(top * height) // TILE - grow)
    x1 = min(cols - 1, max(0, int(right * width - 1)) // TILE + grow)
    y1 = min(rows - 1, max(0, int(bottom * height - 1)) // TILE + grow)
    return [(ix, iy) for iy in range(y0, y1 + 1) for ix in range(x0, x1 + 1)]


def nearest(tiles, view, width, height, limit=MAX_JOB_TILES):
    """At most ``limit`` of ``tiles``, the ones nearest the middle of ``view`` first."""
    cx, cy = (view[0] + view[2]) / 2 * width / TILE, (view[1] + view[3]) / 2 * height / TILE
    return sorted(tiles, key=lambda t: (t[0] + 0.5 - cx) ** 2 + (t[1] + 0.5 - cy) ** 2)[:limit]


def _upscale(image, cancel, progress):
    """``image`` (QImage) upscaled 2x by the Super Resolution model, as a QImage."""
    from PySide6.QtGui import QImage

    from .. import upscale_service as service

    state = service.check_availability()
    if not state.runtime_ok:
        raise RuntimeError(state.runtime_reason or "Super Resolution needs the AI runtime (AI > AI Center).")
    if state.models.get(MODEL):
        raise RuntimeError("The Super Resolution model is not installed. Install it in AI > AI Center.")
    with tempfile.TemporaryDirectory(prefix="image-triage-sr-", ignore_cleanup_errors=True) as scratch:
        source, out = Path(scratch) / "piece.png", Path(scratch) / "piece.2x.png"
        if not image.convertToFormat(QImage.Format.Format_RGB888).save(str(source), "PNG"):
            raise RuntimeError("The piece to upscale could not be written")
        request = service.UpscaleRequest(input_path=source, model_key=MODEL, output_path=out, apply_orientation=False, keep_metadata=False)
        service.run_upscale(request, progress, cancel)
        result = QImage(str(out))
        if result.isNull():
            raise RuntimeError("The upscaled piece could not be read")
        return result.copy()                       # detached from the file, which is deleted with the folder


class RawSuperRes(QObject):
    changed = Signal()
    _done = Signal(int, object, str)
    _step = Signal(int, str)

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._view = None                # (l, t, r, b) of the picture in view while zoomed, else None
        self._source = ""                # the full-resolution picture the tiles were made from
        self._frame = None               # that picture as displayed (QImage), cut from for each job
        self._tiles: OrderedDict = OrderedDict()   # (ix, iy) -> (url, bytes), least recently seen first
        self._job = None                 # (generation, tiles, foreground) while one runs
        self._gen = 0
        self._cancel = threading.Event()
        self._paused = None              # (source, view) Cancel was pressed for: nothing more until either changes
        self._busy = False
        self._message = self._error = ""
        self.upscale = _upscale          # tests replace it
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
        if self._view is None:
            return f"Zoom in to 100% or closer to preview it. An export upscales the whole photo to {size}."
        return f"Shown where you look, as an export makes it ({size})."

    @Property("QVariantList", notify=changed)
    def patches(self):
        """The tiles to lay over the picture: the ones in or next to the view (fractions of the picture)."""
        if not self._tiles or self._view is None or self._frame is None:
            return []
        w, h = self._frame.width(), self._frame.height()
        near = set(tiles_for(self._view, w, h, grow=1))
        out = []
        for (ix, iy), (url, _bytes) in self._tiles.items():
            if (ix, iy) in near:
                x0, y0 = ix * TILE, iy * TILE
                x1, y1 = min(w, x0 + TILE), min(h, y0 + TILE)
                out.append({"url": url, "l": x0 / w, "t": y0 / h, "r": x1 / w, "b": y1 / h})
        return out

    # kept for callers of the single-piece version (the viewer test hooks)
    patch = Property("QVariantMap", lambda self: (self.patches[0] if self.patches else {}), notify=changed)

    def _wanted(self):
        raw = self.raw
        return bool(raw.loaded and not raw.saving and raw._draft.get("superResolution") is True and raw._detail_mode
                    and self._view is not None)

    @Slot(float, float, float, float, bool)
    def setView(self, left, top, right, bottom, zoomed):
        self._view = (left, top, right, bottom) if zoomed and right > left and bottom > top else None
        self._timer.start()
        self.changed.emit()

    @Slot()
    def cancel(self):
        """Stop the upscaling in progress (the overlay's Cancel); nothing more until the view or the picture changes."""
        if self._job is not None:
            self._cancel.set()
            self._gen += 1
            self._job = None
        self._paused = (self._source, self._view)
        self._set(False, "")

    def _set(self, busy, message):
        if (busy, message) != (self._busy, self._message):
            self._busy, self._message = busy, message
        self.changed.emit()

    def _clear(self):
        if self._job is not None:
            self._cancel.set()
            self._gen += 1
            self._job = None
        for url, _bytes in self._tiles.values():
            raw_preview.discard_tile(url)
        self._tiles.clear()
        self._frame = None
        self._source = ""

    def _check(self):
        raw = self.raw
        if raw._draft.get("superResolution") is not True or not raw.loaded:
            if self._tiles or self._job is not None:
                self._clear()
            self._error = ""
            self._set(False, "")
            return
        if not self._wanted():
            if self._job is not None and self._job[2]:
                self._cancel.set()
                self._gen += 1
                self._job = None
            self._set(False, "")
            return
        if not raw._detail:
            self._set(True, "Preparing the full-resolution picture…")          # developed first, then upscaled
            return
        source = raw.property("preview")
        if source != self._source:
            self._clear()                          # an edit made a new picture: the tiles are of the old one
            frame = raw_preview.displayed_frame(source, proof=False)
            if frame.isNull():
                self._set(False, "")
                return
            self._source, self._frame = source, frame
        if self._paused == (self._source, self._view):
            self._set(False, "")
            return
        self._paused = None
        w, h = self._frame.width(), self._frame.height()
        visible = tiles_for(self._view, w, h)
        for key in visible:                        # seen now: kept longest
            if key in self._tiles:
                self._tiles.move_to_end(key)
        missing = [t for t in visible if t not in self._tiles]
        if self._job is not None:
            if missing and not self._job[2]:       # the background ring is in the way of what is in view
                self._cancel.set()
                self._gen += 1
                self._job = None
            else:
                self.changed.emit()
                return
        if missing:
            self._start(nearest(missing, self._view, w, h), foreground=True)
            return
        ring = [t for t in tiles_for(self._view, w, h, grow=1) if t not in self._tiles]
        if ring and self._error == "":
            self._start(nearest(ring, self._view, w, h), foreground=False)
        self._set(False, "")

    def _start(self, tiles, foreground):
        self._cancel.set()
        self._gen += 1
        generation, cancel = self._gen, threading.Event()
        self._cancel = cancel
        frame = self._frame
        w, h = frame.width(), frame.height()
        bx0, by0 = min(t[0] for t in tiles) * TILE, min(t[1] for t in tiles) * TILE
        bx1, by1 = min(w, (max(t[0] for t in tiles) + 1) * TILE), min(h, (max(t[1] for t in tiles) + 1) * TILE)
        x0, y0, x1, y1 = max(0, bx0 - OVERLAP), max(0, by0 - OVERLAP), min(w, bx1 + OVERLAP), min(h, by1 + OVERLAP)
        cut = frame.copy(x0, y0, x1 - x0, y1 - y0)
        self._job = (generation, tiles, foreground)
        if foreground:
            self._set(True, "Upscaling the part in view…")
        upscale = self.upscale

        def progress(update):
            fraction = getattr(update, "fraction", None)
            if foreground and fraction is not None and not cancel.is_set():
                try:
                    self._step.emit(generation, f"Upscaling the part in view… {int(round(float(fraction) * 100))}%")
                except RuntimeError:
                    pass

        def work():
            pieces, error = {}, ""
            try:
                big = upscale(cut, cancel, progress)
                for ix, iy in tiles:
                    tx0, ty0 = ix * TILE, iy * TILE
                    tw, th = min(w, tx0 + TILE) - tx0, min(h, ty0 + TILE) - ty0
                    pieces[(ix, iy)] = big.copy(2 * (tx0 - x0), 2 * (ty0 - y0), 2 * tw, 2 * th)
            except Exception as exc:
                error = "Super Resolution was cancelled" if cancel.is_set() else (str(exc) or "Super Resolution failed")
            if cancel.is_set():
                return
            try:
                self._done.emit(generation, pieces, error)
            except RuntimeError:
                pass                               # the editor was closed meanwhile

        threading.Thread(target=work, name="qml-super-resolution", daemon=True).start()

    def _report(self, generation, text):
        if self._job is not None and generation == self._job[0] and self._busy:
            self._message = text
            self.changed.emit()

    def _arrived(self, generation, pieces, error):
        if self._job is None or generation != self._job[0]:
            return
        self._job = None
        if error:
            self._error = error
            self._set(False, "")
            return
        self._error = ""
        for (ix, iy), image in pieces.items():
            old = self._tiles.pop((ix, iy), None)
            if old is not None:
                raw_preview.discard_tile(old[0])
            url = raw_preview.put_tile(f"{self.raw._owner}/{generation}/{ix}_{iy}", image)
            self._tiles[(ix, iy)] = (url, image.sizeInBytes())
        self._evict()
        self._message = ""
        self._check()                              # the rest of the view, or the ring around it

    def _evict(self):
        """Keep the tiles within the budget: the least recently seen go, never one in view."""
        if self._frame is None:
            return
        keep = set(tiles_for(self._view, self._frame.width(), self._frame.height(), grow=1)) if self._view else set()
        total = sum(b for _url, b in self._tiles.values())
        for key in list(self._tiles):
            if total <= BUDGET_BYTES:
                break
            if key in keep:
                continue
            url, size = self._tiles.pop(key)
            raw_preview.discard_tile(url)
            total -= size
