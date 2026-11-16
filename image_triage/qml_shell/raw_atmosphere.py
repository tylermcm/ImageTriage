"""Atmosphere in the editor: fog by distance. The effect lives in its panel only (settings in the draft, masks built at render
time by ``image_triage.host_effects``); this is the panel's model. The depth map is the one Light Depth uses (same models, same
cache), so a photo that has Light Depth already has it."""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from .. import atmosphere, editor_tools, host_effects, light_depth, photocraft_bridge as bridge
from .raw_light_depth import COMMIT_MS, run_depth_job

_KEPT_MAPS = 4


class RawAtmosphere(QObject):
    changed = Signal()
    _finished = Signal(int, object, str)
    _progress = Signal(int, str)

    def __init__(self, raw) -> None:
        super().__init__(raw)
        self.raw = raw
        self._path = ""
        self._maps = OrderedDict()          # (photo path, depth model) -> (file signature, depth map)
        self._busy = False
        self._message = self._error = ""
        self._waiting = False               # no developed preview yet: the depth map is made as soon as there is one
        self._reason = ""
        self._ticket = 0
        self._cancel = threading.Event()
        self._pending = None                # settings to apply when the depth map arrives
        self._runner = run_depth_job
        self._queued = None                 # the latest settings asked for, to be committed to the photo by the timer
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(COMMIT_MS)
        self._commit_timer.timeout.connect(self.flush)
        self._picture = ("", None)
        self._revision = 0
        self._finished.connect(self._arrived)
        self._progress.connect(self._report)
        raw.changed.connect(self._sync)

    # --- state -----------------------------------------------------------------------------------------------------------
    def _sync(self) -> None:
        if self._path != self.raw._path:
            self._path = self.raw._path
            self._cancel.set()
            self._ticket += 1
            self._busy, self._pending, self._waiting = False, None, False
            self._queued = None
            self._commit_timer.stop()
            self._message = self._error = ""
        elif self._waiting and not self._busy and self._path:
            self._start(self._pending)      # a preview may have arrived
            return
        self.changed.emit()

    def _installed(self, key: str) -> bool:
        return self.raw.lightDepthEditor._installed(key)

    def _model_of(self, settings=None) -> int:
        """The depth model in use: the one the settings ask for, or the best installed one below it (Large is the default)."""
        index = int(round(atmosphere.normalized(self._current() if settings is None else settings)["depthModel"]))
        for candidate in range(index, -1, -1):
            if self._installed(light_depth.DEPTH_MODELS[candidate]):
                return candidate
        return index

    def _depth(self, model=None):
        """The depth map of the model in use for the open photo, or None when it has not been made."""
        index = self._model_of() if model is None else model
        key = (self._path, index)
        kept = self._maps.get(key)
        signature = bridge.file_signature(self._path) if self._path else None
        if kept is not None and kept[0] == signature:
            return kept[1]
        self._maps.pop(key, None)
        # a map another tool (Light Depth) kept for this photo is as good as one made here
        found = light_depth.load_depth(self._path, light_depth.DEPTH_MODELS[index]) if self._path else None
        if found is not None:
            self._maps[key] = (signature, found)
        return found

    busy = Property(bool, lambda self: self._busy, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    waiting = Property(bool, lambda self: self._waiting, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    unavailable = Property(str, lambda self: self._reason, notify=changed)
    ready = Property(bool, lambda self: self._depth() is not None, notify=changed)

    @Property("QVariantMap", notify=changed)
    def settings(self):
        # What was just asked for shows at once, even while its commit to the photo waits a moment.
        return atmosphere.normalized(self._queued if self._queued is not None else self.raw._draft.get("atmosphere"))

    @Property(bool, notify=changed)
    def active(self):
        """The photo has an Atmosphere edit (Amount above zero), whether or not it is switched on."""
        return self.settings["amount"] > 0

    @Property(bool, notify=changed)
    def shown(self):
        return self.settings["shown"] > 0

    @Property("QVariantList", notify=changed)
    def presets(self):
        return list(atmosphere.PRESET_NAMES)

    @Property(int, notify=changed)
    def selectedPreset(self):
        """Which preset the settings still match (an index into ``presets``), or -1 once a slider has moved away from it."""
        settings = self.settings
        for index, preset in enumerate(atmosphere.PRESETS):
            if all(abs(settings[key] - value) < 0.5 for key, value in preset.items()):
                return index
        return -1

    @Property(str, notify=changed)
    def picture(self):
        """The fog as a picture: where it lies on the photo, whiter the denser."""
        import numpy as np

        from .raw_preview import put_thumbnail

        depth = self._depth()
        if depth is None:
            return ""
        settings = self.settings
        key = (self._path, tuple(sorted(settings.items())), id(depth))
        if key != self._picture[1]:
            fog = atmosphere.density(depth, settings, atmosphere.PAD_SIDE, atmosphere.seed_of(depth))
            scene = light_depth.working_depth(depth, atmosphere.PAD_SIDE)
            gray = (0.10 + 0.34 * scene) * (1 - fog) + fog * 0.92          # the scene's depth, with the fog standing out in white
            level = np.floor(scene * 10)                                    # contour lines: where the distance passes a tenth
            lines = np.zeros(scene.shape, bool)
            lines[:, 1:] |= level[:, 1:] != level[:, :-1]
            lines[1:, :] |= level[1:, :] != level[:-1, :]
            gray = np.where(lines, np.minimum(1.0, gray + 0.28), gray)
            pixels = np.clip(np.stack([gray * 0.92, gray * 0.97, gray], axis=-1) * 255, 0, 255).astype(np.uint8)
            height, width = pixels.shape[:2]
            image = QImage(pixels.tobytes(), width, height, width * 3, QImage.Format_RGB888).copy()
            self._revision += 1
            self._picture = (put_thumbnail(f"{self.raw._owner}/atmosphere/{self._revision}", image), key)
        return self._picture[0]

    @Property(float, notify=changed)
    def ceiling(self):
        """Where the top of the fog lies on the picture, 0 (top) .. 1 (bottom), for the Height marker."""
        depth = self._depth()
        return atmosphere.ceiling_of(depth, self.settings) if depth is not None else 0.5

    @Slot(float)
    def setCeiling(self, fraction: float) -> None:
        """The Height marker was moved on the picture: ``fraction`` is where the person put the top of the fog (0 top .. 1 bottom)."""
        depth = self._depth()
        if depth is None or not math.isfinite(fraction):
            return
        self.adjust("height", atmosphere.height_for_ceiling(depth, max(0.0, min(1.0, fraction))))

    def _current(self):
        """What the next change builds on: changes made while the depth map is still being read are not lost."""
        if self._queued is not None:
            return dict(self._queued)
        return dict(self._pending) if self._pending is not None else self.settings

    # --- the depth map ---------------------------------------------------------------------------------------------------
    @Slot()
    def refresh(self) -> None:
        """Ask again whether a depth model is installed (the section was shown, or models were just set up)."""
        self.raw.lightDepthEditor._installed_seen.clear()
        if any(self._installed(key) for key in light_depth.DEPTH_MODELS):
            self._reason = ""
        else:
            self._reason = editor_tools.block_reason("depthMap", editor_tools.installed_models()) or "No depth model is installed."
        self.changed.emit()

    @Slot()
    def prepare(self) -> None:
        """Make the depth map now, so the picture is ready before a control is touched."""
        if self._depth() is None and not self._busy:
            self._start(None)             # nothing is waiting to be applied: only the map is wanted

    def _snapshot(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImageReader

        from .raw_preview import frame_image

        # The picture before edits when there is one: the map should not change because fog was already added.
        for source in (self.raw._baseline, self.raw._preview):
            if not source:
                continue
            image = frame_image(source) if source.startswith("image://") else QImageReader(source).read()
            if not image.isNull():
                return image.scaled(1600, 1600, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        raise ValueError("Wait for the RAW preview before adding Atmosphere.")

    def _start(self, settings) -> None:
        model = self._model_of(settings)
        self.refresh()
        if self._reason:
            self._error, self._pending = self._reason, None
            self.changed.emit()
            return
        try:
            image = self._snapshot()
        except ValueError:
            # Not a failure: the preview is developed in the background and may arrive any moment.
            self._waiting, self._pending, self._error = True, settings, ""
            reason = getattr(self.raw, "_error", "")
            self._message = f"The RAW preview could not be made: {reason}" if reason else "Waiting for the RAW preview…"
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

        threading.Thread(target=work, name="qml-atmosphere", daemon=True).start()

    def _report(self, ticket: int, text: str) -> None:
        if ticket == self._ticket and self._busy:
            self._message = text
            self.changed.emit()

    def _arrived(self, ticket: int, result, error: str) -> None:
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
        self._maps[(self._path, result[3])] = result[:2]
        self._maps.move_to_end((self._path, result[3]))
        while len(self._maps) > _KEPT_MAPS:
            self._maps.popitem(last=False)
        self._message = ""
        if pending is not None:
            self._apply(pending)
        self.changed.emit()

    @Slot()
    def cancel(self) -> None:
        if self._busy:
            self._cancel.set()
            self._ticket += 1
            self._busy, self._pending = False, None
            self._message = "Cancelled"
            self.changed.emit()

    # --- changing the settings -------------------------------------------------------------------------------------------
    @Slot(str, float)
    def adjust(self, key: str, value: float) -> None:
        if key not in atmosphere.RANGES or key in ("shown", "preset", "driftX", "driftY") or not math.isfinite(value):
            return
        if key == "depthModel":
            value = float(round(value))
        low, high = atmosphere.RANGES[key]
        self._queue({**self._current(), key: max(low, min(high, value)), "preset": 0.0})

    @Slot(int)
    def choosePreset(self, index: int) -> None:
        """Set what a preset tile sets (and switch the fog on: a preset has an Amount)."""
        if 0 <= index < len(atmosphere.PRESETS):
            self._queue(atmosphere.with_preset(self._current(), index))
            self.flush()

    @Slot(float, float)
    def drift(self, x: float, y: float) -> None:
        """Move the patches of fog (the Clouds Drift pad): ``x`` and ``y`` are 0..1 across the pad."""
        if not all(math.isfinite(v) for v in (x, y)):
            return
        self._queue({**self._current(), "driftX": max(0.0, min(1.0, x)), "driftY": max(0.0, min(1.0, y)), "preset": 0.0})

    def _queue(self, settings: dict) -> None:
        """Show the change now and commit it to the photo within COMMIT_MS: one commit per pause, not one per mouse event."""
        self._queued = settings
        self.changed.emit()
        if not self._commit_timer.isActive():
            self._commit_timer.start()

    @Slot()
    def flush(self) -> None:
        """Commit what is waiting (the timer does this; ending a gesture does it at once)."""
        self._commit_timer.stop()
        queued, self._queued = self._queued, None
        if queued is not None:
            self._apply(queued)

    def _apply(self, settings: dict) -> None:
        if not self.raw.loaded or self.raw.saving:
            return
        model = self._model_of(settings)
        depth = self._depth(model)
        if depth is None and atmosphere.active(settings):
            self._pending = settings
            if not self._busy:
                self._start(settings)
            return
        settings = atmosphere.normalized({**settings, "depthModel": float(model)})      # the model that made the map in use
        old = atmosphere.normalized(self.raw._draft.get("atmosphere"))
        masks = self.raw._draft.get("masks") or []
        needed = atmosphere.masks_needed(settings)
        others = host_effects.masks_in_use(self.raw._draft, besides="atmosphere")
        if needed > atmosphere.masks_needed(old) and len(masks) + others + needed > atmosphere.MAX_MASKS:
            self._error = f"A photo can have up to {atmosphere.MAX_MASKS} masks. Atmosphere needs {needed} of them: delete some masks to use it."
            self.changed.emit()
            return
        self._error = ""
        self.raw._set_values({"atmosphere": settings}, "Atmosphere",
                             render=atmosphere.active(old) or atmosphere.active(settings))

    @Slot()
    def remove(self) -> None:
        """Take the fog out of the photo: all its settings, as one step."""
        if not self.raw.loaded or self.raw.saving:
            return
        self.cancel()
        self._queued = None
        self._commit_timer.stop()
        self.raw._set_values({"atmosphere": dict(atmosphere.DEFAULTS)}, "Remove Atmosphere")

    @Slot()
    def toggleShown(self) -> None:
        """Switch the fog off and on without losing its settings."""
        if not self.raw.loaded or self.raw.saving:
            return
        settings = self.settings
        on = settings["shown"] <= 0
        self.raw._set_values({"atmosphere": {**settings, "shown": 1.0 if on else 0.0}},
                             "Atmosphere on" if on else "Atmosphere off", render=settings["amount"] > 0)
