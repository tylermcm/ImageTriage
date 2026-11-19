"""Detail > Super Resolution, seen in the RAW editor, as Camera Raw does it.

With the box ticked the photo counts as twice its size (``RawEditor.fullWidth``), and the photo is upscaled once, before any
adjustment: the RAW preview worker gives its demosaiced linear camera RGB (``superresInput``), the model upscales it, and the result
is kept beside the photo as the base companion (``superres_base_encode``; ``<edits>/superres/<photo>.superres``). That one run is
shown under the AI working overlay with its progress. Every edit after it develops from the base: zoomed in, the part of the photo
in view is developed at the base's resolution with the whole recipe (the worker's ``regionRender``, a window of the whole photo), so
an edit never upscales again and only the view is developed.

The base is made again only when the photo it was made from changes (AI Denoise's amount included: the denoised raw is what it is
made from then).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from . import raw_preview

SETTLE_MS = 300           # edits and pans settle this long before the view is developed
SCALE = 2
MODEL = "realesrgan-x2plus"
GROW = 0.15               # the developed part reaches this much of the view's size past each side, for panning
FOLDER = "superres"


def base_path(photo: str | Path, denoised: bool = False) -> Path:
    """Where the photo's Super Resolution base is kept (one made from the denoised raw has its own)."""
    from ..edit_storage import edit_root_for

    photo = Path(photo)
    return edit_root_for(photo.parent) / FOLDER / f"{photo.name}{'.denoised' if denoised else ''}.superres"


def _record(target: Path) -> Path:
    return target.with_name(target.name + ".json")


def base_ready(target: Path, source: str) -> bool:
    """Whether the base at ``target`` was made from ``source`` as it is now."""
    from ..photocraft_bridge import file_signature

    try:
        record = json.loads(_record(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return target.is_file() and record.get("source") == file_signature(source)


def _make_base(source: str, target: Path, cancel, progress):
    """The base of ``source`` (the photo, or its denoised raw), written at ``target``."""
    from .. import upscale_service as service
    from ..photocraft_bridge import file_signature

    state = service.check_availability()
    if not state.runtime_ok:
        raise RuntimeError(state.runtime_reason or "Super Resolution needs the AI runtime (AI > AI Center).")
    if state.models.get(MODEL):
        raise RuntimeError("The Super Resolution model is not installed. Install it in AI > AI Center.")
    exe = raw_preview.executable()
    if not exe:
        raise RuntimeError("Super Resolution needs the RAW preview worker.")
    signature = file_signature(source)
    scratch = Path(tempfile.mkdtemp(prefix="image-triage-srbase-", dir=target.parent if target.parent.is_dir() else None))
    linear = scratch / "linear.f16"
    try:
        connection = raw_preview.Connection(exe)
        connection.timeout = 900
        try:
            if not getattr(connection, "_superres", False):
                raise RuntimeError("Super Resolution needs the updated RAW preview worker.")
            head, _image = connection.call({"op": "superresInput", "path": source, "out": str(linear)})
        finally:
            connection.close()
        if cancel.is_set():
            raise InterruptedError("cancelled")
        target.parent.mkdir(parents=True, exist_ok=True)
        original = os.path.relpath(source, target.parent).replace(os.sep, "/")
        service.run_superres_base(linear, head["width"], head["height"], head["neutral"], head["crop"], original, target,
                                  progress, cancel)
        _record(target).write_text(json.dumps({"source": signature}), encoding="utf-8")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def source_rect(view, crop):
    """The part of the developed photo as recorded (fractions, ``(l, t, r, b)``) that ``view`` (fractions of the finished picture)
    shows; None when the crop straightens or corrects perspective (the developed part could not be laid over the view as it is)."""
    from ..raw_crop import DEFAULT_CROP, view_to_frame
    from ..raw_perspective import active

    c = {**DEFAULT_CROP, **crop}
    if abs(c["angle"]) > 1e-7 or active(c):
        return None
    l, t, r, b = view
    points = [view_to_frame(u, v, c, quarter_only=True) for u, v in ((l, t), (r, b))]
    xs = [c["x"] + u * c["w"] for u, _ in points]
    ys = [c["y"] + v * c["h"] for _, v in points]
    return max(0.0, min(xs)), max(0.0, min(ys)), min(1.0, max(xs)), min(1.0, max(ys))


def view_rect(rect, crop):
    """The inverse of ``source_rect``: where a part of the developed photo is in the finished picture."""
    from ..raw_crop import DEFAULT_CROP, _frame_to_view

    c = {**DEFAULT_CROP, **crop}
    corners = [_frame_to_view((x - c["x"]) / c["w"], (y - c["y"]) / c["h"], c) for x, y in ((rect[0], rect[1]), (rect[2], rect[3]))]
    return min(p[0] for p in corners), min(p[1] for p in corners), max(p[0] for p in corners), max(p[1] for p in corners)


def turned(image, crop):
    """``image`` (a part of the photo as recorded) turned and mirrored as the crop shows the photo."""
    from PySide6.QtGui import QTransform

    from ..raw_crop import DEFAULT_CROP

    c = {**DEFAULT_CROP, **crop}
    if c["rotate"]:
        image = image.transformed(QTransform().rotate(c["rotate"]))
    if c["flipH"] or c["flipV"]:
        image = image.mirrored(bool(c["flipH"]), bool(c["flipV"]))
    return image


class _Regions:
    """The worker that develops parts of the base: its own RAW preview worker, which keeps the base decoded between requests. Only the
    latest request waiting is developed."""

    def __init__(self, deliver):
        self._deliver = deliver
        self._condition = threading.Condition()
        self._queued = None
        self._closing = False
        self._connection = None
        self._thread = None
        self.render = self._render              # tests replace it

    def submit(self, number, request):
        with self._condition:
            self._queued = number, request
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="qml-super-resolution-view")
                self._thread.start()
            self._condition.notify()

    def _render(self, request):
        if self._connection is None:
            exe = raw_preview.executable()
            if not exe:
                raise RuntimeError("Super Resolution needs the RAW preview worker.")
            self._connection = raw_preview.Connection(exe)
            self._connection.timeout = 300
            if not getattr(self._connection, "_region", False):
                raise RuntimeError("Super Resolution needs the updated RAW preview worker.")
        return self._connection.call(request)

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closing or self._queued is not None)
                if self._closing:
                    return
                number, request = self._queued
                self._queued = None
            head, image, error = None, None, ""
            try:
                head, image = self.render(request)
            except Exception as exc:
                error = str(exc) or "Super Resolution could not develop the view"
                connection, self._connection = self._connection, None
                if connection is not None:
                    connection.interrupt()
            if self._closing:
                return
            try:
                self._deliver(number, request, head, image, error)
            except RuntimeError:
                return                                   # the editor was closed meanwhile

    def close(self):
        with self._condition:
            self._closing = True
            self._queued = None
            self._condition.notify()
        if self._connection is not None:
            self._connection.interrupt()


class RawSuperRes(QObject):
    changed = Signal()
    _made = Signal(int, str)
    _step = Signal(int, str)
    _region = Signal(int, object, object, object, str)

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._view = None                # (l, t, r, b) of the picture in view while zoomed, else None
        self._base = None                # the base ready for the photo as it is
        self._making = None              # (source, target) of the base being made
        self._gen = 0
        self._cancel = threading.Event()
        self._paused = None              # the source Cancel was pressed for: no new base until it changes
        self._busy = False
        self._message = self._error = self._note = ""
        self._patch = None               # what is laid over the view: {"url", "l", "t", "r", "b"}, with its source rect and recipe
        self._asked = None               # the region request last sent (rect, recipe text)
        self._requests = 0
        self.make_base = _make_base      # tests replace it
        self._regions = _Regions(lambda *a: self._region.emit(*a))
        self._timer = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._timer.timeout.connect(self._check)
        self._made.connect(self._base_made)
        self._step.connect(self._report)
        self._region.connect(self._region_arrived)
        raw.changed.connect(self._timer.start)

    busy = Property(bool, lambda self: self._busy, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)

    @property
    def regions(self):
        return self._regions

    @Property(str, notify=changed)
    def status(self):
        """The line under the checkbox: what Super Resolution is doing, or how to see it (never that it is done when it is not)."""
        if self._error:
            return self._error
        if self._message:
            return self._message
        size = f"{self.raw.property('fullWidth')} × {self.raw.property('fullHeight')}"
        if self._base is not None:
            return self._note or f"Applied to the whole photo ({size}). Zoom in to 100% or closer to see it."
        return f"The photo will be upscaled once ({size}), as Camera Raw does."

    @Property("QVariantList", notify=changed)
    def patches(self):
        """The developed part of the upscaled photo over the view (fractions of the picture), once there is one."""
        if self._patch is None or self._view is None:
            return []
        return [{k: self._patch[k] for k in ("url", "l", "t", "r", "b")}]

    patch = Property("QVariantMap", lambda self: (self.patches[0] if self.patches else {}), notify=changed)

    @Slot(float, float, float, float, bool)
    def setView(self, left, top, right, bottom, zoomed):
        self._view = (left, top, right, bottom) if zoomed and right > left and bottom > top else None
        self._timer.start()
        self.changed.emit()

    @Slot()
    def cancel(self):
        """Stop making the base (the overlay's Cancel); no new run until the photo changes."""
        if self._making is not None:
            self._cancel.set()
            self._gen += 1
            self._paused = self._making[0]
            self._making = None
        self._busy, self._message = False, ""
        self.changed.emit()

    def _drop_patch(self):
        if self._patch is not None:
            raw_preview.discard_frame(self._patch["url"])
        self._patch, self._asked = None, None

    def _stop(self):
        if self._making is not None:
            self._cancel.set()
            self._gen += 1
            self._making = None
        self._base = None
        self._drop_patch()

    def shutdown(self):
        self._stop()
        self._regions.close()

    def _source(self):
        """``(source, target)``: what the base is made from (the denoised raw when AI Denoise is on and made) and where it is kept."""
        from .. import ai_denoise

        path = self.raw.property("photoPath")
        source = ai_denoise.render_source(path, self.raw._draft)
        return source, base_path(path, denoised=source != path)

    def _check(self):
        raw = self.raw
        if raw._draft.get("superResolution") is not True or not raw.loaded or raw.saving or not raw.property("photoPath"):
            self._stop()
            self._error, self._busy, self._message, self._note = "", False, "", ""
            self.changed.emit()
            return
        source, target = self._source()
        if self._making is not None:
            if self._making == (source, target):
                self.changed.emit()
                return
            self._stop()                              # the photo it was being made from changed
        if self._base != target or not base_ready(target, source):
            self._base = None
            self._drop_patch()
            if base_ready(target, source):
                self._base = target
            elif self._paused == source or self._error:
                self._busy, self._message = False, ""
                self.changed.emit()
                return
            else:
                self._start(source, target)
                return
        self._develop_view()

    def _start(self, source, target):
        self._gen += 1
        generation, cancel = self._gen, threading.Event()
        self._cancel, self._making, self._error, self._paused = cancel, (source, target), "", None
        self._busy, self._message = True, "Upscaling the photo…"
        self.changed.emit()
        make = self.make_base

        def progress(update):
            fraction = getattr(update, "fraction", None)
            if fraction is not None and not cancel.is_set():
                try:
                    self._step.emit(generation, f"Upscaling the photo… {int(round(float(fraction) * 100))}%")
                except RuntimeError:
                    pass

        def work():
            error = ""
            try:
                make(source, target, cancel, progress)
            except Exception as exc:
                error = "Super Resolution was cancelled" if cancel.is_set() else (str(exc) or "Super Resolution failed")
            if cancel.is_set():
                return
            try:
                self._made.emit(generation, error)
            except RuntimeError:
                pass                                   # the editor was closed meanwhile

        threading.Thread(target=work, name="qml-super-resolution", daemon=True).start()

    def _report(self, generation, text):
        if self._making is not None and generation == self._gen:
            self._message = text
            self.changed.emit()

    def _base_made(self, generation, error):
        if generation != self._gen:
            return
        target = self._making[1] if self._making else None
        self._making = None
        self._busy, self._message = False, ""
        self._error = error
        if not error:
            self._base = target
        self.changed.emit()
        self._timer.start()                            # the view, and any edit made while it ran

    def _develop_view(self):
        """Ask for the part of the base in view (and around it) with the recipe as it is, unless it is already over the view."""
        from ..full_render import _without_host_settings
        from .. import host_effects

        raw = self.raw
        if self._view is None:
            self.changed.emit()
            return
        crop = raw.values["hostCrop"]
        inner = source_rect(self._view, crop)
        if inner is None:
            self._note = "Straightened or perspective-corrected photos show Super Resolution in the export only, for now."
            self._drop_patch()
            self.changed.emit()
            return
        self._note = ""
        path = raw.property("photoPath")
        recipe = _without_host_settings({"version": 1, "cameraRaw": host_effects.with_masks(raw._draft, path)})
        text = json.dumps(recipe, sort_keys=True)
        for held in (self._patch, self._asked and {"rect": self._asked[0], "recipe": self._asked[1]}):
            if held and held["recipe"] == text and _covers(held["rect"], inner):
                self.changed.emit()
                return
        l, t, r, b = inner
        gx, gy = (r - l) * GROW, (b - t) * GROW
        rect = [max(0.0, l - gx), max(0.0, t - gy), min(1.0, r + gx), min(1.0, b + gy)]
        self._requests += 1
        self._asked = (rect, text)
        self._regions.submit(self._requests, {"op": "regionRender", "path": str(self._base), "rect": rect, "recipe": recipe})
        self.changed.emit()

    def _region_arrived(self, number, request, head, image, error):
        if self._base is None or request.get("path") != str(self._base):
            return
        if error:
            self._error = error
            self._asked = None
            self.changed.emit()
            return
        if number != self._requests or image is None or image.isNull():
            return                                     # a newer view or edit is on its way
        self._error = ""
        crop = self.raw.values["hostCrop"]
        # The part it developed (whole pixels of the base) as fractions of the photo as recorded, turned as the crop shows it.
        rect = tuple(head["shown"])
        l, t, r, b = view_rect(rect, crop)
        self._drop_patch()
        url = raw_preview.put_frame(f"{self.raw._owner}/superres/{number}", turned(image, crop))
        self._patch = {"url": url, "l": l, "t": t, "r": r, "b": b, "rect": rect, "recipe": json.dumps(request["recipe"], sort_keys=True)}
        self._asked = None
        self.changed.emit()
        self._timer.start()                            # the view may have moved meanwhile


def _covers(outer, inner, slack=1e-6):
    return outer[0] <= inner[0] + slack and outer[1] <= inner[1] + slack and outer[2] >= inner[2] - slack and outer[3] >= inner[3] - slack

