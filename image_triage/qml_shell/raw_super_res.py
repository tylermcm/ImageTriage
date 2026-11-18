"""Detail > Super Resolution, seen in the RAW editor.

Super Resolution itself is applied at output: the export upsizes the whole finished photo 2x (``camera_raw_super_resolution``), which
takes the better part of a minute for a 24 MP raw. The editor shows what it does where it can be seen, at 100% and closer: with the
box ticked the photo counts as twice its size (``RawEditor.fullWidth``), so 100% is one upscaled pixel per screen pixel, and the part
of the photo in view (with a margin, so a small pan needs nothing new) is upscaled by the same model and laid over the picture.
A piece takes a few seconds; the AI working overlay shows it is running. Any edit makes a new full-resolution picture, and with it a
new piece.
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from . import raw_preview

SETTLE_MS = 350           # a pan or zoom settles this long before a new piece is made
MARGIN = 0.25             # of the view's size, on each side
MAX_SOURCE_PIXELS = 1_600_000   # the largest piece upscaled at once (a few seconds)
MODEL = "realesrgan-x2plus"


def piece(view, width, height, margin=MARGIN, limit=MAX_SOURCE_PIXELS):
    """The part of a ``width`` x ``height`` picture to upscale for the view ``(left, top, right, bottom)`` (fractions): the view and a
    margin, kept under ``limit`` pixels (around the view's centre). Returned as fractions."""
    left, top, right, bottom = view
    w, h = max(1e-6, right - left), max(1e-6, bottom - top)
    for grow in (margin, margin / 2, 0.0):
        l, t = max(0.0, left - w * grow), max(0.0, top - h * grow)
        r, b = min(1.0, right + w * grow), min(1.0, bottom + h * grow)
        if (r - l) * width * (b - t) * height <= limit:
            return l, t, r, b
    scale = (limit / max(1.0, w * width * h * height)) ** 0.5            # the view alone is too big: its middle
    cx, cy, hw, hh = (left + right) / 2, (top + bottom) / 2, w * scale / 2, h * scale / 2
    return max(0.0, cx - hw), max(0.0, cy - hh), min(1.0, cx + hw), min(1.0, cy + hh)


def covers(outer, inner, slack=1e-4):
    return (outer[0] <= inner[0] + slack and outer[1] <= inner[1] + slack and outer[2] >= inner[2] - slack
            and outer[3] >= inner[3] - slack)


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
        self._patch = {}                 # {"url", "l", "t", "r", "b", "source"}
        self._job = None                 # (generation, source url, region) while a piece is being made
        self._gen = 0
        self._cancel = threading.Event()
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
    patch = Property("QVariantMap", lambda self: {k: v for k, v in self._patch.items() if k != "source"}, notify=changed)

    def _wanted(self):
        raw = self.raw
        return bool(raw.loaded and not raw.saving and raw._draft.get("superResolution") is True and raw._detail_mode and raw._detail
                    and self._view is not None)

    @Slot(float, float, float, float, bool)
    def setView(self, left, top, right, bottom, zoomed):
        self._view = (left, top, right, bottom) if zoomed and right > left and bottom > top else None
        self._timer.start()

    @Slot()
    def cancel(self):
        if self._busy:
            self._cancel.set()
            self._gen += 1
            self._job, self._busy, self._message = None, False, ""
            self.changed.emit()

    def _clear(self):
        if self._patch:
            raw_preview.discard_frame(self._patch["url"].partition("?")[0])
            self._patch = {}
            self.changed.emit()

    def _check(self):
        if not self._wanted():
            self.cancel()
            self._clear()
            if self._error and self.raw._draft.get("superResolution") is not True:
                self._error = ""
                self.changed.emit()
            return
        source = self.raw.property("preview")
        if self._patch and (self._patch["source"] != source):
            self._clear()                          # an edit made a new picture: the piece is of the old one
        if self._patch and covers((self._patch["l"], self._patch["t"], self._patch["r"], self._patch["b"]), self._view):
            return
        if self._job is not None and self._job[1] == source and covers(self._job[2], self._view):
            return                                 # already being made
        if self._error and self._job is None and self._patch == {} and getattr(self, "_failed_for", None) == (source, self._view):
            return                                 # the same piece failed just now: not tried again until something changes
        self._start(source)

    def _start(self, source):
        self._cancel.set()
        self._gen += 1
        generation, cancel = self._gen, threading.Event()
        self._cancel = cancel
        frame = raw_preview.displayed_frame(source, proof=False)
        if frame.isNull():
            return
        region = piece(self._view, frame.width(), frame.height())
        self._job = (generation, source, region)
        self._busy, self._message, self._error = True, "Upscaling the part in view…", ""
        self.changed.emit()
        x0, y0 = int(region[0] * frame.width()), int(region[1] * frame.height())
        x1, y1 = int(round(region[2] * frame.width())), int(round(region[3] * frame.height()))
        cut = frame.copy(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
        exact = (x0 / frame.width(), y0 / frame.height(), x1 / frame.width(), y1 / frame.height())
        upscale = self.upscale

        def progress(update):
            fraction = getattr(update, "fraction", None)
            if fraction is not None and not cancel.is_set():
                try:
                    self._step.emit(generation, f"Upscaling the part in view… {int(round(float(fraction) * 100))}%")
                except RuntimeError:
                    pass

        def work():
            result, error = None, ""
            try:
                result = (upscale(cut, cancel, progress), exact, source)
            except Exception as exc:
                error = "Super Resolution was cancelled" if cancel.is_set() else (str(exc) or "Super Resolution failed")
            if cancel.is_set():
                return
            try:
                self._done.emit(generation, result, error)
            except RuntimeError:
                pass                               # the editor was closed meanwhile

        threading.Thread(target=work, name="qml-super-resolution", daemon=True).start()

    def _report(self, generation, text):
        if generation == self._gen and self._busy:
            self._message = text
            self.changed.emit()

    def _arrived(self, generation, result, error):
        if generation != self._gen:
            return
        job, self._job, self._busy, self._message = self._job, None, False, ""
        if error or result is None:
            self._error = error
            self._failed_for = (job[1], self._view) if job else None
            self.changed.emit()
            return
        image, region, source = result
        if source != self.raw.property("preview") or not self._wanted():
            self.changed.emit()
            self._timer.start()
            return
        self._clear()
        url = raw_preview.put_frame(f"{self.raw._owner}/superres/{generation}", image)
        proof = dict(part.split("=", 1) for part in source.partition("?")[2].split("&") if "=" in part).get("proof")
        self._patch = {"url": url + (f"?proof={proof}" if proof else ""), "l": region[0], "t": region[1], "r": region[2], "b": region[3],
                       "source": source}
        self._error = ""
        self.changed.emit()
        if self._view is not None and not covers(region, self._view):
            self._timer.start()                    # panned on while this piece was made
