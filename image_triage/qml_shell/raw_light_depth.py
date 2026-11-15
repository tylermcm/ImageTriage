"""The Light Depth section of the RAW editor: a depth map made once per photo, and the settings that light it near and far.

The pixels are not changed here, and no mask is made here. The draft carries only the section's settings (``lightDepth``);
the local masks a render needs are built from them and the cached depth map when the render is requested (see
``image_triage.light_depth``), so the effect shows in this section and nowhere else, and is adjusted in it.
"""
from __future__ import annotations

import math
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Property, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QImageReader

from .. import editor_tools, light_depth, photocraft_bridge as bridge

TOOL = "depthMap"
_KEPT_MAPS = 6
COMMIT_MS = 80        # a slider can move faster than the photo can be developed: changes are committed at most this often
FIRST_AMOUNT = 35     # placing the light for the first time switches it on (Amount 0 would add nothing to the photo)


DEPTH_INPUT_SIZE = 1036      # the short side, in pixels, the photo is given to the depth model at (it works at 518 by default)


def _log_timing(path, info):
    """One line per depth map that was made or read, so the models can be compared (``light-depth-timing.log`` beside the
    application log)."""
    import logging
    import time

    from . import diagnostics

    line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} {info.get('model', '?'):6s} "
            f"{'cached' if info.get('cached') else 'made  '} total {info.get('seconds', 0):6.2f} s  "
            f"load {info.get('load', 0):5.2f} s  inference {info.get('inference', 0):5.2f} s  "
            f"{info.get('device', '')}  input {info.get('input', '')}  {Path(path).name}")
    logging.getLogger("image_triage.light_depth").info(line)
    try:
        with open(diagnostics.log_path().with_name("light-depth-timing.log"), "a", encoding="utf-8") as log:
            log.write(line + "\n")
    except OSError:
        pass


def run_depth_job(image, path, on_progress, cancel, model=0):
    """The depth map of the depth model ``model`` (an index into ``light_depth.DEPTH_MODELS``) for ``path``, and what is
    known about how it was made: the one kept from an earlier session when the photo is unchanged, otherwise a new estimate
    from ``image`` (the developed preview). Runs on a worker thread."""
    import time

    import numpy as np
    from PIL import Image

    from ..depth_maps import light_depth_cache_file
    from ..scene_mask_jobs import run_depth_map

    started = time.perf_counter()
    variant = light_depth.DEPTH_MODELS[int(model)]
    kept = light_depth_cache_file(path, model=variant)
    if kept is not None and kept.is_file():
        try:
            with Image.open(kept) as opened:
                values = np.asarray(opened.convert("L"), dtype=np.uint8)
            info = {"model": variant, "cached": True, "seconds": time.perf_counter() - started}
            _log_timing(path, info)
            return values, info
        except (OSError, ValueError):
            pass  # an unreadable kept map is estimated again below
    with tempfile.TemporaryDirectory(prefix="image-triage-depth-") as scratch:
        source, output = Path(scratch) / "source.png", Path(scratch) / "depth.png"
        if not image.save(str(source), "PNG"):
            raise OSError("Could not prepare the photo for depth estimation")
        made = time.perf_counter()
        reported = run_depth_map(source, output, on_progress, cancel, variant=variant, input_size=DEPTH_INPUT_SIZE)
        seconds = time.perf_counter() - made
        with Image.open(output) as opened:
            if abs(opened.width / opened.height / (image.width() / image.height()) - 1) > .025:
                raise ValueError("The depth map does not match the photo's proportions")
            picture = opened.convert("L")
            picture.thumbnail((light_depth.COVERAGE_SIDE,) * 2, Image.Resampling.BILINEAR)
        values = np.asarray(picture, dtype=np.uint8)
    reported = reported if isinstance(reported, dict) else {}
    info = {"model": variant, "cached": False, "seconds": seconds, "load": reported.get("modelLoadSeconds", 0.0),
            "inference": reported.get("inferenceSeconds", 0.0), "device": reported.get("device", ""),
            "input": "x".join(str(v) for v in (reported.get("modelInput") or ()))}
    _log_timing(path, info)
    if kept is not None:
        try:
            kept.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(values).save(kept)
        except OSError:
            pass  # the map is still used for this session
    return values, info


class RawLightDepth(QObject):
    changed = Signal()
    _finished = Signal(int, object, str)
    _progress = Signal(int, str)

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._path = ""
        self._maps = OrderedDict()  # (photo path, depth model) -> (file signature, depth map)
        self._info = {}             # (photo path, depth model) -> what is known about how the map was made
        self._busy = False
        self._message = self._error = ""
        self._waiting = False           # no developed preview yet: the depth map is made as soon as there is one
        self._reason = ""
        self._ticket = 0
        self._cancel = threading.Event()
        self._pending = None  # settings to apply when the depth map arrives
        self._runner = run_depth_job
        self._queued = None             # the latest settings asked for, to be committed to the photo by the timer
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(COMMIT_MS)
        self._commit_timer.timeout.connect(self.flush)
        self._pad = ("", None)
        self._revision = 0
        self._open = False              # the Light Depth section is open: its handles are drawn on the photo
        self._picking = False           # the next click on the photo picks the depth
        self._finished.connect(self._arrived)
        self._progress.connect(self._report)
        raw.changed.connect(self._sync)

    def _sync(self):
        if self._path != self.raw._path:
            self._path = self.raw._path
            self._cancel.set()
            self._ticket += 1
            self._busy, self._pending, self._waiting = False, None, False
            self._queued = None
            self._picking = False
            self._commit_timer.stop()
            self._message = self._error = ""
        elif self._waiting and not self._busy and self._path:
            self._start(self._pending)      # a preview may have arrived
            return
        self.changed.emit()

    def _model_of(self, settings=None):
        """The index of the depth model that ``settings`` (by default what the next change builds on) asks for."""
        return int(round(light_depth.normalized(self._current() if settings is None else settings)["depthModel"]))

    def _depth(self, model=None):
        """The depth map of the model in use (or of ``model``) for the open photo, or None when it has not been made."""
        key = (self._path, self._model_of() if model is None else model)
        kept = self._maps.get(key)
        if kept is None:
            return None
        if kept[0] != bridge.file_signature(self._path):
            del self._maps[key]
            return None
        return kept[1]

    busy = Property(bool, lambda self: self._busy, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    waiting = Property(bool, lambda self: self._waiting, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    unavailable = Property(str, lambda self: self._reason, notify=changed)
    ready = Property(bool, lambda self: self._depth() is not None, notify=changed)
    open = Property(bool, lambda self: self._open, notify=changed)
    picking = Property(bool, lambda self: self._picking, notify=changed)

    @Property("QVariantMap", notify=changed)
    def settings(self):
        # What was just asked for shows at once, even while its commit to the photo waits a moment.
        return light_depth.normalized(self._queued if self._queued is not None else self.raw._draft.get("lightDepth"))

    @Property(int, notify=changed)
    def selectedModel(self):
        """The depth model in use, or being switched to (an index into ``light_depth.DEPTH_MODELS``)."""
        return self._model_of()

    @Property("QVariantList", notify=changed)
    def models(self):
        """The depth models: ``key``, ``label`` and whether each is installed."""
        return [{"key": key, "label": label, "installed": self._installed(key)}
                for key, label in zip(light_depth.DEPTH_MODELS, light_depth.DEPTH_MODEL_LABELS)]

    @Property(str, notify=changed)
    def modelInfo(self):
        """How the depth map in use was made: the model, how long it took, or that it came from the saved maps."""
        info = self._info.get((self._path, self._model_of()))
        if not info or self._depth() is None:
            return ""
        label = light_depth.DEPTH_MODEL_LABELS[self._model_of()]
        if info.get("cached"):
            return f"{label} depth model: kept from before"
        text = f"{label} depth model: made in {info['seconds']:.1f} s"
        if info.get("load"):
            text += f" ({info['load']:.1f} s loading the model, {info.get('inference', 0):.1f} s estimating)"
        return text

    @staticmethod
    def _installed(key):
        if key == "small":
            return not editor_tools.block_reason(TOOL, editor_tools.installed_models())
        try:
            from ..ai_model import depth_variant_installation

            return bool(depth_variant_installation(key).is_installed)
        except Exception:
            return False

    @Slot(int)
    def setModel(self, index):
        """Use another depth model (Small, Base or Large) for this photo's depth map."""
        if 0 <= index < len(light_depth.DEPTH_MODELS):
            self.adjust("depthModel", float(index))
            self.flush()

    @Property(bool, notify=changed)
    def active(self):
        """The photo has a Light Depth edit (Amount above zero), whether or not it is switched on."""
        return self.settings["amount"] > 0

    @Property(bool, notify=changed)
    def shown(self):
        return self.settings["shown"] > 0

    @Property(str, notify=changed)
    def pad(self):
        """The depth map as a picture (near is bright), with what lies at the chosen depth tinted amber: what is left as it is."""
        import numpy as np

        from .raw_preview import put_thumbnail

        depth = self._depth()
        if depth is None:
            return ""
        settings = self.settings
        key = (self._path, tuple(sorted(settings.items())), id(depth))
        if key != self._pad[1]:
            scene = light_depth.working_depth(depth, light_depth.PAD_SIDE)
            slab = (light_depth.coverage(depth, settings, light_depth.PAD_SIDE).astype(np.float32) / 255)[:, :, None]
            base = (.12 + .5 * scene)[:, :, None]
            pixels = np.clip((base * (1 - .45 * slab) + slab * np.array([.55, .38, .06], dtype=np.float32)) * 255, 0, 255).astype(np.uint8)
            height, width = pixels.shape[:2]
            image = QImage(pixels.tobytes(), width, height, width * 3, QImage.Format_RGB888).copy()
            self._revision += 1
            self._pad = (put_thumbnail(f"{self.raw._owner}/lightdepth/{self._revision}", image), key)
        return self._pad[0]

    def _current(self):
        """What the next change builds on: changes made while the depth map is still being read are not lost."""
        if self._queued is not None:
            return dict(self._queued)
        return dict(self._pending) if self._pending is not None else self.settings

    @Slot()
    def refresh(self):
        """Ask again whether the depth model is installed (the section was opened, or models were just set up)."""
        index = self._model_of()
        if index == 0:
            self._reason = editor_tools.block_reason(TOOL, editor_tools.installed_models())
        else:
            self._reason = ("" if self._installed(light_depth.DEPTH_MODELS[index])
                            else f"The {light_depth.DEPTH_MODEL_LABELS[index]} depth model is not installed.")
        self.changed.emit()

    @Slot()
    def prepare(self):
        """Make the depth map now, so the pad is ready before a control is touched."""
        if self._depth() is None and not self._busy:
            self._start(self._pending)

    @Slot(bool)
    def setOpen(self, on):
        """The section was opened or closed: its handles are drawn on the photo while it is open."""
        if not on:
            self._picking = False
        if on != self._open or not on:
            self._open = on
            self.changed.emit()

    @Slot(bool)
    def setPicking(self, on):
        """Arm or disarm the depth dropper: the next click on the photo picks the depth under it."""
        on = bool(on) and self.raw.loaded and not self.raw.saving
        if on != self._picking:
            self._picking = on
            if on:
                self.prepare()
            self.changed.emit()

    @Slot(str, float)
    def adjust(self, key, value):
        if key not in light_depth.RANGES or key in ("x", "y", "shown") or not math.isfinite(value):
            return
        if key == "depthModel":
            value = float(round(value))
        low, high = light_depth.RANGES[key]
        self._queue({**self._current(), key: max(low, min(high, value))})

    def _first_use(self, settings):
        """Placing the light for the first time switches it on (Amount 0 would add nothing to the photo)."""
        if settings.get("amount", 0) <= 0 and "lightDepth" not in self.raw._draft:
            settings["amount"] = FIRST_AMOUNT
        return settings

    @Slot(float, float)
    def place(self, x, y):
        """Move the light's position on the photo (the centre of its pool, when it has a size below 100)."""
        if not all(math.isfinite(v) for v in (x, y)):
            return
        settings = {**self._current(), "x": max(0.0, min(1.0, x)), "y": max(0.0, min(1.0, y))}
        self._queue(self._first_use(settings))

    @Slot(float, float)
    def pick(self, x, y):
        """A click on the photo while picking: the depth under that point becomes the depth of the split."""
        if not self._picking or not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            return
        self._picking = False
        depth = self._depth()
        settings = {**self._current(), "x": x, "y": y}
        # Without a map the distance is not known yet; it is read from the map when it arrives.
        settings["depth"] = light_depth.depth_at(depth, x, y) if depth is not None else None
        self._queue(self._first_use(settings))
        self.flush()

    def _queue(self, settings):
        """Show the change now and commit it to the photo within COMMIT_MS: one commit per pause, not one per mouse event."""
        self._queued = settings
        self.changed.emit()
        if not self._commit_timer.isActive():
            self._commit_timer.start()

    @Slot()
    def flush(self):
        """Commit what is waiting (the timer does this; ending a gesture does it at once)."""
        self._commit_timer.stop()
        queued, self._queued = self._queued, None
        if queued is not None:
            self._apply(queued)

    @Slot()
    def remove(self):
        """Take the light out of the photo: all its settings, as one step."""
        if not self.raw.loaded or self.raw.saving:
            return
        self.cancel()
        self._queued = None
        self._commit_timer.stop()
        self.raw._set_values({"lightDepth": dict(light_depth.DEFAULTS)}, "Remove Light Depth")

    @Slot()
    def toggleShown(self):
        """Switch the light off and on without losing its settings."""
        if not self.raw.loaded or self.raw.saving:
            return
        settings = self.settings
        on = settings["shown"] <= 0
        self.raw._set_values({"lightDepth": {**settings, "shown": 1.0 if on else 0.0}},
                             "Light Depth on" if on else "Light Depth off", render=settings["amount"] > 0)

    @Slot()
    def cancel(self):
        if self._busy:
            self._cancel.set()
            self._ticket += 1
            self._busy, self._pending = False, None
            self._message = "Cancelled"
            self.changed.emit()

    def _apply(self, settings):
        if not self.raw.loaded or self.raw.saving:
            return
        depth = self._depth(self._model_of(settings))
        # The map is needed to resolve a depth that was asked for before it existed, and by any render of the effect.
        if depth is None and (settings.get("depth") is None or light_depth.active(settings)):
            self._pending = settings
            if not self._busy:
                self._start(settings)
            return
        if settings.get("depth") is None:
            settings = {**settings, "depth": light_depth.depth_at(depth, settings["x"], settings["y"])}
        settings = light_depth.normalized(settings)
        old = light_depth.normalized(self.raw._draft.get("lightDepth"))
        masks = self.raw._draft.get("masks") or []
        needed = light_depth.masks_needed(settings)
        if needed > light_depth.masks_needed(old) and len(masks) + needed > light_depth.MAX_MASKS:
            self._error = f"A photo can have up to 16 masks. Light Depth needs {needed} of them: delete some masks to use it."
            self.changed.emit()
            return
        self._error = ""
        # Only the settings go in the draft. The masks the renderer needs are built from them for each render.
        self.raw._set_values({"lightDepth": settings}, "Light Depth",
                             render=light_depth.active(old) or light_depth.active(settings))

    def _snapshot(self):
        from .raw_preview import frame_image

        # The picture before edits when there is one: the map should not change because the light was already added.
        for source in (self.raw._baseline, self.raw._preview):
            if not source:
                continue
            image = frame_image(source) if source.startswith("image://") else QImageReader(source).read()
            if not image.isNull():
                return image.scaled(1600, 1600, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        raise ValueError("Wait for the RAW preview before adding Light Depth.")

    def _start(self, settings):
        model = self._model_of(settings)
        self.refresh()
        if self._reason:
            self._error, self._pending = self._reason, None
            self.changed.emit()
            return
        try:
            image = self._snapshot()
        except ValueError:
            # Not a failure: the preview is developed in the background and may arrive any moment. Say what is awaited, keep what
            # was asked for, and try again when the editor changes (the panel asks again on every change that has no error).
            self._waiting, self._pending, self._error = True, settings, ""
            reason = getattr(self.raw, "_error", "")
            self._message = (f"The RAW preview could not be made: {reason}" if reason else "Waiting for the RAW preview…")
            self.changed.emit()
            return
        self._waiting = False
        self._ticket += 1
        ticket, path = self._ticket, self._path
        signature = bridge.file_signature(path)
        self._cancel = cancel = threading.Event()
        self._pending = settings
        self._busy, self._message, self._error = True, "Reading the scene's depth…", ""
        self.changed.emit()
        runner = self._runner

        def progress(text):
            if not cancel.is_set():
                try:
                    self._progress.emit(ticket, text)
                except RuntimeError:
                    pass

        def work():
            result, error = None, ""
            try:
                made = runner(image, path, progress, cancel, model)
                depth, info = made if isinstance(made, tuple) else (made, {})
                result = (signature, depth, info, model)
            except Exception as exc:
                error = str(exc) or "The depth map could not be made"
            try:
                self._finished.emit(ticket, result, error)
            except RuntimeError:
                pass  # the editor was closed meanwhile

        threading.Thread(target=work, name="qml-light-depth", daemon=True).start()

    def _report(self, ticket, text):
        if ticket == self._ticket and self._busy:
            self._message = text
            self.changed.emit()

    def _arrived(self, ticket, result, error):
        if ticket != self._ticket:
            return
        self._busy = False
        pending, self._pending = self._pending, None
        if not error and result[0] != bridge.file_signature(self._path):
            error = "The photo changed while its depth was being read. Try again."
        if error:
            self._error, self._message = error, ""
            self.changed.emit()
            return
        key = (self._path, result[3])
        self._maps[key] = result[:2]
        self._info[key] = result[2]
        self._maps.move_to_end(key)
        while len(self._maps) > _KEPT_MAPS:
            gone, _ = self._maps.popitem(last=False)
            self._info.pop(gone, None)
        self._message = ""
        if pending is not None:
            self._apply(pending)
        self.changed.emit()
