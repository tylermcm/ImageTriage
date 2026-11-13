"""The Light Depth section of the RAW editor: a depth map made once per photo, and a light placed in it.

The pixels are not changed here. The light's reach becomes the coverage of an ordinary local mask named "Light Depth"
(see ``image_triage.light_depth``); the draft also carries the settings that produced it so the sliders, undo and a
later session can find them again.
"""
from __future__ import annotations

import math
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot
from PySide6.QtGui import QImage, QImageReader

from .. import editor_tools, light_depth, photocraft_bridge as bridge
from ..camera_raw_recipe_masks import masks_valid
from .raw_masks import encode_raster

TOOL = "depthMap"
_KEPT_MAPS = 6


def run_depth_job(image, path, on_progress, cancel):
    """The depth map for ``path``: the one kept from an earlier session when the photo is unchanged, otherwise a new
    estimate from ``image`` (the developed preview). Runs on a worker thread."""
    import numpy as np
    from PIL import Image

    from ..depth_maps import light_depth_cache_file
    from ..scene_mask_jobs import run_depth_map

    kept = light_depth_cache_file(path)
    if kept is not None and kept.is_file():
        try:
            with Image.open(kept) as opened:
                return np.asarray(opened.convert("L"), dtype=np.uint8)
        except (OSError, ValueError):
            pass  # an unreadable kept map is estimated again below
    with tempfile.TemporaryDirectory(prefix="image-triage-depth-") as scratch:
        source, output = Path(scratch) / "source.png", Path(scratch) / "depth.png"
        if not image.save(str(source), "PNG"):
            raise OSError("Could not prepare the photo for depth estimation")
        run_depth_map(source, output, on_progress, cancel)
        with Image.open(output) as opened:
            if abs(opened.width / opened.height / (image.width() / image.height()) - 1) > .025:
                raise ValueError("The depth map does not match the photo's proportions")
            picture = opened.convert("L")
            picture.thumbnail((1024, 1024), Image.Resampling.BILINEAR)
        values = np.asarray(picture, dtype=np.uint8)
    if kept is not None:
        try:
            kept.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(values).save(kept)
        except OSError:
            pass  # the map is still used for this session
    return values


class RawLightDepth(QObject):
    changed = Signal()
    _finished = Signal(int, object, str)
    _progress = Signal(int, str)

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._path = ""
        self._maps = OrderedDict()  # photo path -> (file signature, depth map)
        self._busy = False
        self._message = self._error = ""
        self._reason = ""
        self._ticket = 0
        self._cancel = threading.Event()
        self._pending = None  # settings to apply when the depth map arrives
        self._runner = run_depth_job
        self._pad = ("", None)
        self._revision = 0
        self._finished.connect(self._arrived)
        self._progress.connect(self._report)
        raw.changed.connect(self._sync)

    def _sync(self):
        if self._path != self.raw._path:
            self._path = self.raw._path
            self._cancel.set()
            self._ticket += 1
            self._busy, self._pending = False, None
            self._message = self._error = ""
        self.changed.emit()

    def _depth(self):
        kept = self._maps.get(self._path)
        if kept is None:
            return None
        if kept[0] != bridge.file_signature(self._path):
            del self._maps[self._path]
            return None
        return kept[1]

    busy = Property(bool, lambda self: self._busy, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    unavailable = Property(str, lambda self: self._reason, notify=changed)
    ready = Property(bool, lambda self: self._depth() is not None, notify=changed)
    active = Property(bool, lambda self: light_depth.find_mask(self.raw._draft.get("masks")) >= 0, notify=changed)

    @Property("QVariantMap", notify=changed)
    def settings(self):
        return light_depth.normalized(self.raw._draft.get("lightDepth"))

    @Property(str, notify=changed)
    def pad(self):
        """The depth map as a picture (near is bright), tinted where the light falls."""
        import numpy as np

        from .raw_preview import put_thumbnail

        depth = self._depth()
        if depth is None:
            return ""
        settings = self.settings
        key = (self._path, tuple(sorted(settings.items())), id(depth))
        if key != self._pad[1]:
            scene = light_depth.working_depth(depth, light_depth.PAD_SIDE)
            light = light_depth.coverage(depth, settings, light_depth.PAD_SIDE).astype(np.float32) / 255
            lit = min(1.0, .35 + settings["amount"] / 100)
            warm = settings["warmth"] / 100
            tint = np.array([1 + .25 * max(0, warm), 1.0, 1 + .25 * max(0, -warm)], dtype=np.float32)
            base = (.12 + .5 * scene)[:, :, None]
            pixels = np.clip((base + light[:, :, None] * lit * .36 * tint) * 255, 0, 255).astype(np.uint8)
            height, width = pixels.shape[:2]
            image = QImage(pixels.tobytes(), width, height, width * 3, QImage.Format_RGB888).copy()
            self._revision += 1
            self._pad = (put_thumbnail(f"{self.raw._owner}/lightdepth/{self._revision}", image), key)
        return self._pad[0]

    def _current(self):
        """What the next change builds on: changes made while the depth map is still being read are not lost."""
        return dict(self._pending) if self._pending is not None else self.settings

    @Slot()
    def refresh(self):
        """Ask again whether the depth model is installed (the section was opened, or models were just set up)."""
        self._reason = editor_tools.block_reason(TOOL, editor_tools.installed_models())
        self.changed.emit()

    @Slot()
    def prepare(self):
        """Make the depth map now, so the pad is ready before a control is touched."""
        if self._depth() is None and not self._busy:
            self._start(None)

    @Slot(str, float)
    def adjust(self, key, value):
        if key not in ("amount", "softness", "warmth", "reach", "focus", "depth") or not math.isfinite(value):
            return
        low, high = light_depth.RANGES[key]
        self._apply({**self._current(), key: max(low, min(high, value))})

    @Slot(float, float)
    def place(self, x, y):
        """Put the light on a point of the photo: it takes that point's distance in the scene."""
        if not all(math.isfinite(v) for v in (x, y)):
            return
        x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
        depth = self._depth()
        settings = {**self._current(), "x": x, "y": y}
        if depth is not None:
            settings["depth"] = light_depth.depth_at(depth, x, y)
        else:
            # Without a map the distance is not known yet; it is read from the map when it arrives.
            settings["depth"] = None
        self._apply(settings)

    @Slot()
    def remove(self):
        """Take the light out of the photo: its mask and its settings, as one step."""
        if not self.raw.loaded or self.raw.saving:
            return
        self.cancel()
        rows = list(self.raw._draft.get("masks", []))
        index = light_depth.find_mask(rows)
        if index >= 0:
            rows.pop(index)
        self.raw._set_values({"masks": rows, "lightDepth": dict(light_depth.DEFAULTS)}, "Remove Light Depth")

    @Property(bool, notify=changed)
    def shown(self):
        rows = self.raw._draft.get("masks", [])
        index = light_depth.find_mask(rows)
        return index >= 0 and rows[index].get("enabled", True)

    @Slot()
    def toggleShown(self):
        """Switch the light off and on without losing where it was placed."""
        if not self.raw.loaded or self.raw.saving:
            return
        from copy import deepcopy

        rows = deepcopy(list(self.raw._draft.get("masks", [])))
        index = light_depth.find_mask(rows)
        if index >= 0:
            rows[index]["enabled"] = not rows[index].get("enabled", True)
            self.raw._set_values({"masks": rows}, "Light Depth on" if rows[index]["enabled"] else "Light Depth off")

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
        depth = self._depth()
        if depth is None:
            self._pending = settings
            if not self._busy:
                self._start(settings)
            return
        if settings.get("depth") is None:
            settings = {**settings, "depth": light_depth.depth_at(depth, settings["x"], settings["y"])}
        settings = light_depth.normalized(settings)
        rows = list(self.raw._draft.get("masks", []))
        exists = light_depth.find_mask(rows) >= 0
        if not exists and settings["amount"] <= 0:
            # Nothing is lit yet: remember where the light is without adding an empty mask.
            self.raw._set_values({"lightDepth": settings}, "Light Depth", render=False)
            return
        if not exists and len(rows) >= 16:
            self._error = "A photo can have up to 16 masks. Delete one to add Light Depth."
            self.changed.emit()
            return
        shape = encode_raster(light_depth.coverage(depth, settings))
        rows = light_depth.apply(rows, settings, shape)
        if not masks_valid(rows):
            self._error = "Light Depth could not be applied to this photo."
            self.changed.emit()
            return
        self._error = ""
        self.raw._set_values({"masks": rows, "lightDepth": settings}, "Light Depth")

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
        self.refresh()
        if self._reason:
            self._error, self._pending = self._reason, None
            self.changed.emit()
            return
        try:
            image = self._snapshot()
        except ValueError as error:
            self._error, self._pending = str(error), None
            self.changed.emit()
            return
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
                result = (signature, runner(image, path, progress, cancel))
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
        self._maps[self._path] = result
        self._maps.move_to_end(self._path)
        while len(self._maps) > _KEPT_MAPS:
            self._maps.popitem(last=False)
        self._message = ""
        if pending is not None:
            self._apply(pending)
        self.changed.emit()
