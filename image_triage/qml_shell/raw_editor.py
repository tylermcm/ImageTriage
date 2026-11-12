"""The popout's RAW draft and its private PhotoCraft render jobs."""
from __future__ import annotations

import math
from copy import deepcopy
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot

from .. import edit_storage, photocraft_bridge as bridge
from .histograms import HistogramService
from . import raw_preview
from ..raw_crop import DEFAULT_CROP, RATIOS, bounds as crop_bounds, valid as crop_valid

_native_context = threading.local()


class _NativeJob:
    def __init__(self):
        self.stopped = threading.Event()
        self.proc = None

    def cancel(self):
        self.stopped.set()
        if self.proc and self.proc.process.poll() is None:
            try:
                self.proc.process.kill()
            except OSError:
                pass


def _image_url(path):
    return path if path.startswith("image://") else QUrl.fromLocalFile(path).toString() if path else ""


FIELDS = {"exposure": (-5, 5), **dict.fromkeys(
    ("contrast", "highlights", "shadows", "whites", "blacks", "temperature", "tint", "vibrance", "saturation",
     "curveHighlights", "curveLights", "curveDarks", "curveShadows", "gradeBalance",
     "texture", "clarity", "dehaze", "vignetteAmount", "vignetteRoundness", "calibShadowTint"),
    (-100, 100),
)}
FIELDS["gradeBlending"] = (0, 100)
FIELDS.update(dict.fromkeys(
    ("vignetteMidpoint", "vignetteFeather", "vignetteHighlights", "grainAmount", "grainSize", "grainRoughness",
     "sharpenDetail", "sharpenMasking", "noiseLuminance", "noiseLuminanceDetail", "noiseColor", "noiseColorDetail"),
    (0, 100),
))
FIELDS.update(sharpenAmount=(0, 150), sharpenRadius=(0.5, 3))
CURVES = ("pointCurve", "pointCurveRed", "pointCurveGreen", "pointCurveBlue")
MIXER = ("hslHue", "hslSat", "hslLum")
WHEELS = ("gradeShadows", "gradeMidtones", "gradeHighlights", "gradeGlobal")
DEFAULTS = {
    "masks": [],
    "retouch": [],
    "hostCrop": DEFAULT_CROP,
    **dict.fromkeys(FIELDS, 0),
    "gradeBlending": 50,
    "curveSplits": [25, 50, 75],
    "vignetteMidpoint": 50, "vignetteFeather": 50, "vignetteStyle": "highlightPriority",
    "grainSize": 25, "grainRoughness": 50,
    "glow": {"amount": 0, "style": "diffusion", "range": 50, "spread": 50, "warmth": 0},
    "sharpenRadius": 1, "sharpenDetail": 25, "noiseLuminanceDetail": 50, "noiseColorDetail": 50,
    "calibHue": [0, 0, 0], "calibSat": [0, 0, 0], "superResolution": False,
    **{key: [] for key in CURVES},
    **{key: [0] * 8 for key in MIXER},
    **{key: {"hue": 0, "sat": 0, "lum": 0} for key in WHEELS},
}


def _recipe(settings: dict) -> dict:
    return {"version": bridge.RECIPE_VERSION, "cameraRaw": settings}


class RawEditor(QObject):
    changed = Signal()
    saved = Signal(str, str)
    _finished = Signal(int, str, object, str)

    def __init__(self, *, memory=False, parent=None):
        super().__init__(parent)
        self._memory = memory
        self._path = ""
        self._original = {}
        self._draft = {}
        self._preview = ""
        self._baseline = ""
        self._undo = []
        self._redo = []
        self._sessions = {}
        self._histograms = HistogramService(parent=self)
        self._files = None
        self._error = ""
        self._running = False
        self._saving = False
        self._loaded = False
        self._revision = 0
        self._queued = None
        self._owner = uuid4().hex
        self._proxy = None
        self._proxy_pending = False
        self._published_revision = -1
        self._full_size = [0, 0]
        self._preview_size = [0, 0]
        self._baseline_size = [0, 0]
        self._detail_size = [0, 0]
        self._crop_editing = False
        self._gesture = False
        self._gesture_recorded = False
        self._detail_mode = False
        self._detail = ""
        self._native_job = None
        self._native_action = ""
        self._detail_timer = QTimer(self, singleShot=True, interval=500)
        self._detail_timer.timeout.connect(self._request_detail)
        self._timer = QTimer(self, singleShot=True, interval=60)
        self._timer.timeout.connect(self._start_queued)
        self._finished.connect(self._arrived)
        from .raw_masks import RawMasks
        self._mask_editor = RawMasks(self)
        from .raw_retouch import RawRetouch
        self._retouch_editor=RawRetouch(self)

    retouchEditor=Property(QObject,lambda self:self._retouch_editor,constant=True)

    maskEditor = Property(QObject, lambda self: self._mask_editor, constant=True)

    active = Property(bool, lambda self: bool(self._path), notify=changed)
    busy = Property(bool, lambda self: self._running or self._queued is not None or self._proxy_pending, notify=changed)
    saving = Property(bool, lambda self: self._saving, notify=changed)
    loaded = Property(bool, lambda self: self._loaded, notify=changed)
    dirty = Property(bool, lambda self: self._draft != self._original, notify=changed)

    def unsaved_names(self) -> list[str]:
        """The photos with RAW adjustments that were never saved with Done: the one open now and the ones stepped away from (their drafts are only in memory)."""
        import os

        paths = [path for path, session in self._sessions.items() if session[1] != session[0]]
        if self._path and self._loaded and self._draft != self._original:
            paths.append(self._path)
        return [os.path.basename(path) for path in paths]

    unsavedCount = Property(int, lambda self: len(self.unsaved_names()), notify=changed)

    @Slot(result=str)
    def unsavedSummary(self) -> str:
        names = self.unsaved_names()
        return ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    @Property("QVariantMap", notify=changed)
    def values(self):
        values = deepcopy(DEFAULTS)
        values.update(deepcopy(self._draft))
        for key in ("glow", *WHEELS):
            values[key] = {**DEFAULTS[key], **self._draft.get(key, {})}
        return values

    def _cropped_url(self, path, clip):
        url = _image_url(path)
        from ..raw_crop import active as crop_active
        if url.startswith("image://") and clip["w"] > 0 and crop_active(self.values["hostCrop"]):
            return url + "?crop=" + ",".join(str(clip[key]) for key in ("x", "y", "w", "h"))
        return url

    preview = Property(str, lambda self: self._cropped_url(self._detail if self._detail_mode and self._detail else self._preview, self.previewClip), notify=changed)
    baseline = Property(str, lambda self: self._cropped_url(self._baseline, self.baselineClip), notify=changed)
    detailPending = Property(bool, lambda self: self._detail_mode and not self._detail, notify=changed)
    def _display_size(self):
        if self._crop_editing or min(self._full_size) <= 0:
            return self._full_size
        return crop_bounds(self.values["hostCrop"], *self._full_size)[2:]

    fullWidth = Property(int, lambda self: self._display_size()[0], notify=changed)
    fullHeight = Property(int, lambda self: self._display_size()[1], notify=changed)
    originalWidth = Property(int, lambda self: self._full_size[0], notify=changed)
    originalHeight = Property(int, lambda self: self._full_size[1], notify=changed)
    cropEditing = Property(bool, lambda self: self._crop_editing, notify=changed)

    @Property("QVariantMap", notify=changed)
    def previewClip(self):
        size = self._detail_size if self._detail_mode and self._detail else self._preview_size
        if self._crop_editing or min(size) <= 0:
            return {"x": 0, "y": 0, "w": 0, "h": 0}
        x, y, w, h = crop_bounds(self.values["hostCrop"], *size)
        return {"x": x, "y": y, "w": w, "h": h}
    @Property("QVariantMap", notify=changed)
    def baselineClip(self):
        if self._crop_editing or min(self._baseline_size) <= 0:
            return {"x": 0, "y": 0, "w": 0, "h": 0}
        x, y, w, h = crop_bounds(self.values["hostCrop"], *self._baseline_size)
        return {"x": x, "y": y, "w": w, "h": h}

    histogramPath = Property(str, lambda self: self._preview or self._path, notify=changed)
    baselinePath = Property(str, lambda self: self._baseline or self._path, notify=changed)
    histograms = Property(QObject, lambda self: self._histograms, constant=True)
    history = Property("QStringList", lambda self: [label for label, _ in reversed(self._undo)], notify=changed)
    canUndo = Property(bool, lambda self: bool(self._undo) and not self._saving, notify=changed)
    canRedo = Property(bool, lambda self: bool(self._redo) and not self._saving, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)

    @Slot(str)
    def open(self, path: str) -> None:
        if self._saving or path == self._path or not path:
            return
        self._stop_detail()
        self._proxy_pending = False
        self._detail_size = [0, 0]
        if hasattr(self, "_proxy_request"):
            del self._proxy_request
        self._gesture = self._gesture_recorded = False
        if self._path and self._loaded:
            self._sessions[self._path] = (
                self._original, self._draft, self._preview, self._baseline,
                self._undo, self._redo, self._full_size, self._preview_size, self._baseline_size,
            )
        self._full_size = [0, 0]
        self._preview_size = self._baseline_size = [0, 0]
        self._revision += 1
        self._timer.stop()
        self._queued = None
        self._error = ""
        self._path = path
        self._original = {}
        self._draft = {}
        self._preview = self._baseline = ""
        self._undo = []
        self._redo = []
        self._loaded = self._memory
        restored = self._sessions.pop(path, None)
        if restored is not None:
            (self._original, self._draft, self._preview, self._baseline,
             self._undo, self._redo, self._full_size, self._preview_size, self._baseline_size) = restored
            if self._preview.startswith("image://") and not raw_preview.has_frame(self._preview):
                self._preview = ""
            if self._baseline.startswith("image://") and not raw_preview.has_frame(self._baseline):
                self._baseline = ""
            self._loaded = True
            if not self._memory and (self.dirty or raw_preview.executable()):
                self._enqueue("preview")
        elif Path(path).suffix.lower() not in bridge.RAW_SUFFIXES:
            self._loaded = False
            self._error = "Choose a RAW photo to use these adjustments."
        elif not self._memory:
            self._enqueue("load")
        self.changed.emit()

    @Slot(str, float)
    def adjust(self, key: str, value: float) -> None:
        if not self._loaded or self._saving or key not in FIELDS or not math.isfinite(value):
            return
        low, high = FIELDS[key]
        value = max(low, min(high, value))
        self._set_value(key, value, key.capitalize())

    def _set_value(self, key, value, label):
        self._set_values({key: value}, label)

    def _set_values(self, updates, label, render=True):
        if not self._loaded or self._saving:
            return
        draft = dict(self._draft)
        for key, value in updates.items():
            if value == draft.get(key, DEFAULTS[key]):
                continue
            if value == DEFAULTS[key] and key not in self._original:
                draft.pop(key, None)
            else:
                draft[key] = deepcopy(value)
        if draft == self._draft:
            return
        if not self._gesture or not self._gesture_recorded:
            self._undo.append((label, dict(self._draft)))
            self._gesture_recorded = self._gesture
        self._redo.clear()
        self._draft = draft
        if render:
            self._draft_changed()
        else:
            self.changed.emit()

    @Slot(bool)
    def setCropEditing(self, enabled):
        if self._crop_editing != enabled:
            self._crop_editing = enabled
            self.changed.emit()

    @Slot(float, float, float, float)
    def setCrop(self, x, y, width, height):
        crop = {**self.values["hostCrop"], "x": x, "y": y, "w": width, "h": height}
        if crop_valid(crop):
            self._set_values({"hostCrop": crop}, "Crop", render=False)

    @Slot(str)
    def setCropRatio(self, ratio):
        if ratio not in RATIOS:
            return
        crop = {**self.values["hostCrop"], "ratio": ratio}
        if ratio != "Free":
            aspect = self._full_size[0] / max(1, self._full_size[1]) if self._full_size[0] else 1
            desired = aspect if ratio == "Original" else float(ratio.split(":")[0]) / float(ratio.split(":")[1])
            normalized = desired / aspect
            width, height = crop["w"], crop["h"]
            if width / height > normalized:
                width = height * normalized
            else:
                height = width / normalized
            crop.update(x=crop["x"]+(crop["w"]-width)/2, y=crop["y"]+(crop["h"]-height)/2, w=width, h=height)
        self._set_values({"hostCrop": crop}, "Crop ratio", render=False)

    @Slot()
    def resetCrop(self):
        self._set_values({"hostCrop": dict(DEFAULT_CROP)}, "Reset crop", render=False)

    @Slot()
    def beginGesture(self):
        if not self._gesture:
            self._gesture = True
            self._gesture_recorded = False
            self._mask_editor.changed.emit()

    @Slot()
    def endGesture(self):
        changed = self._gesture_recorded
        self._gesture = self._gesture_recorded = False
        self._mask_editor.changed.emit()
        if changed and not self._memory and raw_preview.executable():
            self._enqueue("preview")
        if self._detail_mode:
            self._detail_timer.start()

    @Slot(bool)
    def setDetailMode(self, enabled):
        if self._detail_mode == enabled:
            return
        self._detail_mode = enabled
        if enabled and self._loaded and not self._memory:
            self._detail_timer.start()
        elif not enabled:
            self._stop_detail()
        self.changed.emit()

    def _stop_detail(self):
        self._detail_timer.stop()
        if self._native_action == "detail" and self._native_job:
            self._native_job.cancel()
        self._discard_preview(self._detail)
        self._detail = ""

    def _request_detail(self):
        if self._detail_mode and self._loaded and not self._saving and not self._gesture:
            self._enqueue("detail")
            self.changed.emit()

    @Slot()
    def asShotWhiteBalance(self):
        self._set_values({"temperature": 0, "tint": 0}, "White balance: As Shot")

    @Slot(float, float)
    def pickWhiteBalance(self, x, y):
        if not self._loaded or self._saving or not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            return
        crop = self.values["hostCrop"]
        if not self._crop_editing:
            x, y = crop["x"] + x*crop["w"], crop["y"] + y*crop["h"]
        self._enqueue("whiteBalance", (x, y))
        self.changed.emit()

    @Slot(str, float)
    def setGlow(self, component, value):
        if component not in ("amount", "range", "spread", "warmth") or not math.isfinite(value):
            return
        settings = dict(self.values["glow"])
        settings[component] = max(-100 if component == "warmth" else 0, min(100, value))
        self._set_value("glow", settings, "Glow")

    @Slot(str, str)
    def setEffectStyle(self, effect, style):
        if effect == "vignette" and style in ("highlightPriority", "colorPriority", "paintOverlay"):
            self._set_value("vignetteStyle", style, "Vignette style")
        elif effect == "glow" and style in ("diffusion", "bloom", "halation"):
            settings = dict(self.values["glow"])
            settings["style"] = style
            self._set_value("glow", settings, "Glow style")

    @Slot(int, str, float)
    def setCalibration(self, channel, component, value):
        if channel not in range(3) or component not in ("calibHue", "calibSat") or not math.isfinite(value):
            return
        values = list(self._draft.get(component, DEFAULTS[component]))
        values[channel] = max(-100, min(100, value))
        self._set_value(component, values, "Calibration")

    @Slot(bool)
    def setSuperResolution(self, enabled):
        self._set_value("superResolution", enabled, "Super Resolution")

    @Slot(str, "QVariantList")
    def setCurve(self, channel, points):
        if channel not in CURVES:
            return
        try:
            values = [[float(x), float(y)] for x, y in points]
        except (TypeError, ValueError, OverflowError):
            return
        if values and not 2 <= len(values) <= 16:
            return
        if any(not math.isfinite(v) or not 0 <= v <= 255 for point in values for v in point):
            return
        if any(right[0] - left[0] < 1 for left, right in zip(values, values[1:])):
            return
        if values == [[0, 0], [255, 255]] and not self._draft.get(channel):
            return
        self._set_value(channel, values, "Tone curve")

    @Slot(int, str, float)
    def setMixer(self, channel, component, value):
        if component not in MIXER or channel not in range(-1, 8) or not math.isfinite(value):
            return
        values = list(self._draft.get(component, DEFAULTS[component]))
        value = max(-100, min(100, value))
        if channel == -1:
            values = [value] * 8
        else:
            values[channel] = value
        self._set_value(component, values, "Color mixer")

    @Slot(str, float, float, float)
    def setGrading(self, wheel, hue, saturation, luminance):
        if wheel not in WHEELS or not all(math.isfinite(v) for v in (hue, saturation, luminance)):
            return
        value = {"hue": hue % 360, "sat": max(0, min(100, saturation)), "lum": max(-100, min(100, luminance))}
        self._set_value(wheel, value, "Color grading")

    @Slot()
    def undo(self) -> None:
        self._step_history(self._undo, self._redo)

    @Slot()
    def redo(self) -> None:
        self._step_history(self._redo, self._undo)

    @Slot(int)
    def restoreHistory(self, index: int) -> None:
        if self._saving or index < 0 or index >= len(self._undo):
            return
        for _ in range(index + 1):
            label, draft = self._undo.pop()
            self._redo.append((label, self._draft))
            self._draft = draft
        self._draft_changed()

    @Slot()
    def reset(self) -> None:
        if not self._loaded or self._saving:
            return
        draft = dict(self._draft)
        for key, default in DEFAULTS.items():
            # Global Reset keeps authored masks, as it did before the Mask tab.
            if key in ("masks","retouch"):
                continue
            if key in self._original:
                draft[key] = deepcopy(default)
            else:
                draft.pop(key, None)
        if draft == self._draft:
            return
        self._undo.append(("Reset", dict(self._draft)))
        self._redo.clear()
        self._draft = draft
        self._draft_changed()

    def _step_history(self, source, destination):
        if self._saving or not source:
            return
        label, draft = source.pop()
        destination.append((label, self._draft))
        self._draft = draft
        self._draft_changed()

    def _draft_changed(self):
        self._error = ""
        self._stop_detail()
        if not self._memory:
            self._enqueue("preview")
        self.changed.emit()
        if self._detail_mode and not self._gesture:
            self._detail_timer.start()

    @Slot()
    def cancel(self) -> None:
        if self._saving:
            return
        self._stop_detail()
        if self._native_job:
            self._native_job.cancel()
        if self._proxy:
            self._proxy.close()
            self._proxy.deleteLater()
            self._proxy = None
        self._proxy_pending = False
        self._revision += 1
        self._timer.stop()
        self._queued = None
        self._discard_preview(self._preview)
        self._path = self._preview = self._error = ""
        self._baseline = ""
        self._undo.clear()
        self._redo.clear()
        self._draft = self._original = {}
        self._loaded = False
        self._gesture = self._gesture_recorded = False
        raw_preview.clear_frames(self._owner)
        if not self._running and not self._sessions:
            self._clear_files()
        self.changed.emit()

    @Slot()
    def done(self) -> None:
        if not self._loaded or self._saving:
            return
        if self._draft == self._original or self._memory:
            self.cancel()
            return
        self._saving = True
        self._stop_detail()
        self._proxy_pending = False
        if self._proxy:
            self._proxy.close()
            self._proxy.deleteLater()
            self._proxy = None
        self._error = ""
        self._enqueue("save")
        self.changed.emit()

    def _clear_files(self):
        if self._files is not None:
            self._files.cleanup()
            self._files = None

    def shutdown(self):
        self._sessions.clear()
        self.cancel()

    def _enqueue(self, action: str, sample=None) -> None:
        if self._saving and action != "save":
            # Nothing may start while a save is running: it would move the revision on, the save's result would be taken
            # for a stale one, and the editor would stay "saving" (refusing open, cancel, done and every slider) for good.
            return
        if action != "detail":
            self._revision += 1
        if action not in ("preview", "detail"):
            self._proxy_pending = False
            if hasattr(self, "_proxy_request"):
                del self._proxy_request
        if action in ("preview", "whiteBalance", "maskSample") and raw_preview.executable():
            self._proxy_pending = True
            # A bounded dispatch interval during dragging, not a trailing-edge
            # debounce that waits until the person stops moving the pointer.
            self._proxy_request = (self._revision, self._path, deepcopy(self._original), deepcopy(self._draft),
                                   "draft" if self._gesture else "settled", sample)
            if not self._timer.isActive():
                self._timer.start()
            return
        self._queued = (self._revision, action, self._path, dict(self._original), dict(self._draft), sample)
        if action == "preview":
            self._timer.start()
        else:
            self._timer.stop()
            self._start_queued()

    def _start_queued(self) -> None:
        if self._proxy_pending and hasattr(self, "_proxy_request"):
            if self._proxy is None:
                self._proxy = raw_preview.PreviewWorker(raw_preview.executable(), self)
                self._proxy.completed.connect(self._proxy_arrived)
                self._proxy.sampled.connect(self._proxy_sampled)
                self._proxy.maskSampled.connect(self._mask_sampled)
                self._proxy.coverageReady.connect(self._coverage_arrived)
                self._proxy.failed.connect(self._proxy_failed)
            request = self._proxy_request
            del self._proxy_request
            if request[0] == self._revision and not self._saving:
                coverage=self._mask_editor.current
                if any(p.get("shape",{}).get("type") in ("colorRange","luminanceRange") for p in [coverage,*coverage.get("parts",[])]):
                    self._proxy.submit(*request,coverage=coverage)
                else:self._proxy.submit(*request)
        if self._running or self._queued is None:
            return
        revision, action, path, original, draft, sample = self._queued
        self._queued = None
        if revision != self._revision:
            return
        if action in ("preview", "whiteBalance", "detail") and self._files is None:
            self._files = tempfile.TemporaryDirectory(prefix="image-triage-raw-")
        directory = self._files.name if self._files else ""
        self._running = True
        job = self._native_job = _NativeJob()
        self._native_action = action
        self.changed.emit()

        def work():
            _native_context.job = job
            try:
                result = self._work(action, path, original, draft, directory, sample)
                if action == "detail":
                    # Decode on this worker, then publish pixels. QML must not
                    # keep a Windows file handle open while a new edit deletes
                    # the old full-resolution temporary PNG.
                    from PySide6.QtGui import QImageReader
                    try:
                        reader = QImageReader(result)
                        image = reader.read()
                        del reader
                        if image.isNull():
                            raise ValueError("The full-resolution preview could not be decoded.")
                    finally:
                        Path(result).unlink(missing_ok=True)
                    result = image
                self._finished.emit(revision, action, result, "")
            except Exception as error:
                self._finished.emit(revision, action, None, str(error) or "PhotoCraft could not complete the RAW request.")

        threading.Thread(target=work, name="qml-raw-editor", daemon=True).start()

    def _proxy_arrived(self, revision, result):
        if (not self._loaded or self._saving or revision < self._published_revision
                or (revision != self._revision and not self._gesture)):
            return
        (header, image), (base_header, base_image), elapsed, path = result
        if path != self._path:
            return
        self._published_revision = revision
        self._full_size = header["fullSize"]
        self._preview_size = [image.width(), image.height()]
        self._baseline_size = [base_image.width(), base_image.height()]
        self._proxy_pending = revision != self._revision
        self._error = ""
        self._discard_preview(self._preview)
        self._preview = raw_preview.put_frame(f"{self._owner}/{revision}/after", image)
        self._baseline = raw_preview.put_frame(f"{self._owner}/{revision}/before", base_image)
        self._histograms.publish_preview(self._preview, raw_preview.histogram(header))
        self._histograms.publish_preview(self._baseline, raw_preview.histogram(base_header))
        self.changed.emit()
        if self._detail_mode and not self._detail and not self._gesture:
            self._detail_timer.start()

    def _proxy_failed(self, revision, error):
        if revision == self._revision:
            self._proxy_pending = False
            self._error = error
            self.changed.emit()

    def _proxy_sampled(self, revision, result):
        if revision != self._revision or not self._loaded or self._saving:
            return
        self._proxy_pending = False
        image, (x, y) = result
        from PySide6.QtCore import QRect
        px, py = min(image.width()-1, int(x*image.width())), min(image.height()-1, int(y*image.height()))
        try:
            values = _white_balance_from_image(image.copy(QRect(px-2,py-2,5,5).intersected(image.rect())))
            self._set_values(values, "White balance sample")
        except ValueError as error:
            self._error = str(error)
        self.changed.emit()

    def _mask_sampled(self, revision, result):
        if revision != self._revision or not self._loaded or self._saving:
            return
        self._proxy_pending = False
        try:
            self._mask_editor.applySample(*result)
        except (ValueError,KeyError,TypeError) as error:
            self._error = str(error)
        self.changed.emit()

    def _coverage_arrived(self,revision,result):
        if revision==self._revision and self._loaded and not self._saving:
            self._mask_editor.acceptCoverage(*result)

    @staticmethod
    def _work(action, path, original, draft, directory, sample=None):
        if bridge.sidecar_pcraft_path(path).exists():
            raise ValueError("This photo already has a PhotoCraft project. Open it in PhotoCraft to edit it.")
        state, recipe, reason = bridge.recipe_state(path)
        if state == bridge.RECIPE_INVALID:
            raise ValueError(reason)
        settings = dict(recipe["cameraRaw"]) if recipe else {}
        if action == "load":
            return settings, str(bridge.saved_render_path(path) or "")
        if settings != original:
            raise ValueError("The saved recipe changed. Reopen Edit RAW before applying more edits.")
        folder = str(Path(path).parent)
        saving = action == "save"
        write_root = str(edit_storage.ensure_edit_root(folder)) if saving else directory
        proc = bridge.launch_photocraft(None, read_root=folder, write_root=write_root,
                                       extra_args=("--raw-workspace",) if saving else ())
        try:
            job = getattr(_native_context, "job", None)
            if job:
                job.proc = proc
                if job.stopped.is_set():
                    job.cancel()
                    raise RuntimeError("RAW request cancelled")
            source = proc.path_within_root(path)
            if not source:
                raise ValueError("The photo is outside the editor's read folder.")
            bridge.require_masks_support({"recipe": _recipe(draft)}, getattr(proc.control, "camera_raw_masks_supported", False) is True)
            bridge.require_retouch_support({"recipe": _recipe(draft)}, getattr(proc.control, "camera_raw_retouch_supported", False) is True)
            if not saving:
                if not proc.control.full_render_supported:
                    raise ValueError("This PhotoCraft build cannot render RAW recipes.")
                # Keep non-interactive host features in the recipe for save, outside this preview job.
                from ..full_render import _without_host_settings

                out = uuid4().hex
                settings = {} if action == "whiteBalance" else draft
                reply = proc.control.call("app.render", {"source": source, "recipe": _without_host_settings(_recipe(settings)), "out": out})
                name = reply.get("out")
                if name not in (out + ".png", out + ".tif", out + ".tiff"):
                    raise ValueError("PhotoCraft returned an unexpected preview path.")
                preview = Path(directory) / name
                if action == "whiteBalance":
                    try:
                        return _white_balance_sample(preview, *sample)
                    finally:
                        preview.unlink(missing_ok=True)
                try:
                    return _display_preview(preview)
                finally:
                    preview.unlink(missing_ok=True)
            if any(key in draft or key in original for key in ("hostCrop", "masks", "retouch")):
                from ..full_render import _without_host_settings
                from ..raw_crop import publish
                signature = bridge.file_signature(path)
                if not signature:
                    raise ValueError("The original photo could not be read")
                out = uuid4().hex
                reply = proc.control.call("app.render", {"source": source, "recipe": _without_host_settings(_recipe(draft)), "out": out})
                name = reply.get("out")
                if name not in (out + ".png", out + ".tif", out + ".tiff"):
                    raise ValueError("PhotoCraft returned an unexpected crop render path")
                rendered = Path(write_root) / name
                try:
                    _, current, _ = bridge.recipe_state(path)
                    if (current or {}).get("cameraRaw", {}) != original:
                        raise ValueError("The saved recipe changed while the crop was rendering")
                    return publish(path, rendered, draft, signature)
                finally:
                    rendered.unlink(missing_ok=True)
            if not proc.control.render_manifest_supported:
                raise ValueError("This PhotoCraft build cannot publish RAW previews to the grid.")
            bridge.require_super_resolution_support(
                {"recipe": _recipe(draft)}, getattr(proc.control, "camera_raw_super_resolution_supported", False),
            )
            signature = bridge.file_signature(path)
            if not signature:
                raise ValueError("The original RAW could not be read.")
            opened = proc.control.app_open(source, replace=True, camera_raw={
                "recipe": _recipe(original), "signature": signature,
                "recipePath": bridge.recipe_path(path).name,
                "displayPath": bridge.display_render_path(path).name,
                "manifestPath": bridge.render_manifest_path(path).name,
            })
            if not (opened.get("cameraRaw") or {}).get("dialog"):
                raise ValueError("PhotoCraft could not develop this RAW.")
            proc.control.call("ui.menu.invoke", {"id": "filter.cameraRaw",
                "params": {"ui": {"set": {key: draft.get(key, default) for key, default in DEFAULTS.items() if key not in ("hostCrop", "masks", "retouch")}}}})
            previous_render = bridge.saved_render_path(path)
            result = proc.control.call("ui.cameraRaw", {"done": True})
            if result.get("open"):
                raise ValueError("PhotoCraft could not finish saving the RAW recipe.")
            changed = (result.get("result") or {}).get("changed")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                render = bridge.saved_render_path(path)
                if render is not None and (render != previous_render or changed is False):
                    return str(render)
                if not proc.is_running():
                    break
                time.sleep(0.05)
            raise ValueError("The RAW recipe was saved, but PhotoCraft did not publish its preview.")
        finally:
            proc.shutdown()

    def _arrived(self, revision, action, result, error):
        self._running = False
        self._native_action = ""
        self._native_job = None
        if action == "save":
            self._saving = False                      # whatever else happened meanwhile, the save is over
        if revision == self._revision:
            if action != "detail" or self._detail_mode:
                self._error = error
            if not error and action == "load":
                self._original, self._preview = result
                self._baseline = self._preview
                self._draft = dict(self._original)
                self._loaded = True
                # A saved display is already cropped; redevelop from the RAW before editing.
                self._preview = self._baseline = ""
                self._enqueue("preview")
            elif not error and action == "detail" and self._detail_mode:
                self._detail_size = _preview_dimensions(result)
                self._discard_preview(self._detail)
                self._detail = (raw_preview.put_frame(f"{self._owner}/{revision}/detail", result)
                                if not isinstance(result, str) else result)
            elif not error and action == "preview":
                self._discard_preview(self._preview)
                self._preview = result
                self._preview_size = _preview_dimensions(result)
                if min(self._full_size) <= 0:
                    self._full_size = list(self._preview_size)
            elif not error and action == "whiteBalance":
                self._set_values(result, "White balance sample")
            elif not error and action == "save":
                path = self._path
                self.cancel()
                self.saved.emit(path, result)
        if action == "detail" and isinstance(result, str) and result and result != self._detail:
            self._discard_preview(result)
        elif revision != self._revision and action == "preview" and result:
            self._discard_preview(result)
        if not self._path and not self._sessions:
            self._clear_files()
        if not self._timer.isActive():
            self._start_queued()
        self.changed.emit()

    def _discard_preview(self, path):
        if path and path.startswith("image://"):
            raw_preview.discard_frame(path)
            return
        if path and self._files and Path(path).parent == Path(self._files.name):
            Path(path).unlink(missing_ok=True)


def _preview_dimensions(image):
    from PySide6.QtGui import QImageReader
    size = QImageReader(image).size() if isinstance(image, str) else image.size()
    return [size.width(), size.height()]


def _display_preview(path):
    """Keep native-resolution pixels in a display copy Qt can decode safely.

    PhotoCraft renders 16-bit RGB PNGs. Qt expands those to eight bytes per
    pixel, exceeding its default allocation limit on 45 MP cameras. Pillow
    decodes them into display RGB without changing the saved RAW recipe.
    """
    from PIL import Image, ImageOps
    from PySide6.QtGui import QImageReader

    target = Path(path).with_name(Path(path).stem + ".display.png")
    try:
        with Image.open(path) as source:
            profile = source.info.get("icc_profile")
            image = ImageOps.exif_transpose(source).convert("RGB")
            if profile:
                from io import BytesIO
                from PIL import ImageCms
                display_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
                image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(BytesIO(profile)),
                    display_profile, outputMode="RGB", renderingIntent=1, flags=0x2000)
                profile = display_profile.tobytes()
            # Keep full size whenever it fits, including the 8256 x 5504 Z7.
            # Larger sensors still need a bounded display fallback.
            limit = QImageReader.allocationLimit() * 1024 * 1024
            if limit > 0 and image.width * image.height * 4 > limit * .9:
                scale = math.sqrt(limit * .9 / (image.width * image.height * 4))
                image = image.resize((max(1, int(image.width * scale)),
                                      max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
            image.save(target, "PNG", icc_profile=profile, compress_level=1)
        return str(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _white_balance_sample(path, x, y):
    """Use PhotoCraft's relative-gain solver on a neutral sample from an unadjusted develop."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColorSpace, QImage, QImageReader

    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isEmpty():
        raise ValueError("The white balance sample could not be read.")
    px = min(size.width() - 1, int(x * size.width()))
    py = min(size.height() - 1, int(y * size.height()))
    reader.setClipRect(QRect(px - 2, py - 2, 5, 5).intersected(QRect(0, 0, size.width(), size.height())))
    image = reader.read()
    if image.isNull():
        raise ValueError("The white balance sample could not be read.")
    return _white_balance_from_image(image)


def _white_balance_from_image(image):
    from PySide6.QtGui import QColorSpace, QImage
    image = image.convertToFormat(QImage.Format.Format_RGBA64)
    if image.colorSpace().isValid():
        image = image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgbLinear))
    else:
        image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
        image = image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgbLinear))
    samples = []
    for row in range(image.height()):
        for column in range(image.width()):
            color = image.pixelColor(column, row)
            if color.alphaF() > 0:
                samples.append((color.redF(), color.greenF(), color.blueF()))
    if not samples:
        raise ValueError("Choose an opaque, neutral area for white balance.")
    r, g, b = (sum(sample[channel] for sample in samples) / len(samples) for channel in range(3))
    if min(r, g, b) <= 1e-6 or max(r, g, b) >= 0.99:
        raise ValueError("Choose a neutral area away from clipped highlights and deep shadows.")
    temperature = 100 * (b - r) / (0.35 * (r + b))
    tint = 400 * (1 - (2 * r * b / (r + b)) / g)
    return {"temperature": max(-100, min(100, temperature)), "tint": max(-100, min(100, tint))}
