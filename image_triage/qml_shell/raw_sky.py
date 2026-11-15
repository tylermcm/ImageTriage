"""The Sky Replacement section of the RAW editor: pick a sky picture, find the photo's sky once, and set how it blends.

The pixels are replaced by PhotoCraft's renderer (the recipe's ``sky`` setting, see ``image_triage.sky_replace``). This
controller finds the sky matte with the landscape model, keeps it for the photo, and writes matte plus sliders into the
draft as one undoable setting, throttled like Light Depth so a slider drag does not queue a render per mouse event.
"""
from __future__ import annotations

import math
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Property, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QImage, QImageReader

from .. import editor_tools, photocraft_bridge as bridge, sky_replace

TOOL = "sceneMasks"
_KEPT_MATTES = 6
COMMIT_MS = 80


def run_sky_matte(image, on_progress, cancel):
    """The raster matte (white = sky) of ``image``, the developed preview. Runs on a worker thread."""
    import numpy as np
    from PIL import Image

    from ..scene_mask_jobs import Cancelled, run_scene_masks
    from .raw_masks import encode_raster

    with tempfile.TemporaryDirectory(prefix="image-triage-sky-") as scratch:
        root = Path(scratch)
        source = root / "source.png"
        if not image.save(str(source), "PNG"):
            raise OSError("Could not prepare the photo for sky detection")
        payload = run_scene_masks(source, root / "manifest.json", on_progress, cancel)
        if cancel.is_set():
            raise Cancelled()
        for item in payload.get("categories", []):
            if item.get("id") != "sky":
                continue
            path = (root / item["file"]).resolve()
            if path.parent != root.resolve():
                raise ValueError("Unexpected model mask path")
            with Image.open(path) as opened:
                if abs(opened.width / opened.height / (image.width() / image.height()) - 1) > .025:
                    raise ValueError("The model mask does not match the photo's proportions")
                picture = opened.convert("L")
                picture.thumbnail((sky_replace.MATTE_SIDE, sky_replace.MATTE_SIDE), Image.Resampling.BILINEAR)
                values = np.asarray(picture, dtype=np.uint8)
            if float((values >= 128).mean()) < .005:
                break
            return encode_raster(values)
    raise ValueError("No sky found in this photo.")


class RawSky(QObject):
    changed = Signal()
    _finished = Signal(int, object, str)
    _progress = Signal(int, str)

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._path = ""
        self._mattes = OrderedDict()   # photo path -> (file signature, matte)
        self._busy = self._waiting = False
        self._message = self._error = self._reason = ""
        self._ticket = 0
        self._cancel = threading.Event()
        self._pending = None           # settings to apply when the matte arrives
        self._queued = None            # the latest settings asked for, committed by the timer
        self._runner = run_sky_matte
        self._library_root = None      # tests point this somewhere else
        self._thumbs = {}
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(COMMIT_MS)
        self._commit_timer.timeout.connect(self.flush)
        self._finished.connect(self._arrived)
        self._progress.connect(self._report)
        raw.changed.connect(self._sync)

    # -- state shown by the panel
    busy = Property(bool, lambda self: self._busy, notify=changed)
    waiting = Property(bool, lambda self: self._waiting, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    unavailable = Property(str, lambda self: self._reason, notify=changed)
    active = Property(bool, lambda self: sky_replace.active(self.raw._draft.get("sky")), notify=changed)

    @Property("QVariantMap", notify=changed)
    def settings(self):
        """The sliders (never the matte: it is large and the panel has no use for it)."""
        current = self._queued if self._queued is not None else self.raw._draft.get("sky")
        return {k: v for k, v in sky_replace.normalized(current).items() if k != "mask"}

    @Property("QVariantList", notify=changed)
    def skies(self):
        from .raw_preview import put_thumbnail

        sky_replace.seed_bundled(self._library_root)

        result = []
        for item in sky_replace.library(self._library_root):
            thumb = self._thumbs.get(item["path"])
            if thumb is None:
                image = QImageReader(item["path"])
                image.setScaledSize(image.size().scaled(160, 160, Qt.KeepAspectRatio))
                picture = image.read()
                if picture.isNull():
                    continue
                thumb = self._thumbs[item["path"]] = put_thumbnail(f"{self.raw._owner}/sky/{len(self._thumbs)}", picture)
            result.append({**item, "thumb": thumb, "chosen": item["path"] == self.settings["source"]})
        return result

    def _sync(self):
        if self._path != self.raw._path:
            self._path = self.raw._path
            self._cancel.set()
            self._ticket += 1
            self._busy = self._waiting = False
            self._pending = self._queued = None
            self._commit_timer.stop()
            self._message = self._error = ""
        elif self._waiting and not self._busy and self._path:
            self._start(self._pending)       # a preview may have arrived
            return
        self.changed.emit()

    def _matte(self):
        kept = self._mattes.get(self._path)
        if kept is None:
            sky = self.raw._draft.get("sky")
            mask = sky.get("mask") if isinstance(sky, dict) else None
            return mask       # a matte saved with the photo is as good as one found now
        if kept[0] != bridge.file_signature(self._path):
            del self._mattes[self._path]
            return None
        return kept[1]

    @Slot()
    def refresh(self):
        self._reason = editor_tools.block_reason(TOOL, editor_tools.installed_models())
        self.changed.emit()

    # -- choosing and adjusting
    def _current(self):
        if self._queued is not None:
            return dict(self._queued)
        return dict(self._pending) if self._pending is not None else self.settings

    @Slot(str)
    def choose(self, path):
        if not path or not sky_replace.library_has(path, self._library_root):
            return
        current = self._current()
        settings = {**current, "source": path}
        if not sky_replace.active(self.raw._draft.get("sky")) and current["amount"] <= 0:
            settings.update(amount=sky_replace.FIRST_AMOUNT, colorMatch=sky_replace.FIRST_MATCH,
                            relight=sky_replace.FIRST_RELIGHT, fadeEdge=sky_replace.FIRST_FADE)
        self._queue(settings)

    @Slot(str, float)
    def adjust(self, key, value):
        if key not in sky_replace.RANGES or not math.isfinite(value):
            return
        low, high = sky_replace.RANGES[key]
        self._queue({**self._current(), key: float(max(low, min(high, value)))})

    @Slot(bool)
    def setFlip(self, flip):
        self._queue({**self._current(), "flip": bool(flip)})

    def _queue(self, settings):
        self._queued = settings
        self.changed.emit()
        if not self._commit_timer.isActive():
            self._commit_timer.start()

    @Slot()
    def flush(self):
        self._commit_timer.stop()
        queued, self._queued = self._queued, None
        if queued is not None:
            self._apply(queued)

    @Slot()
    def remove(self):
        if not self.raw.loaded or self.raw.saving:
            return
        self.cancel()
        self._queued = None
        self._commit_timer.stop()
        self.raw._set_values({"sky": {}}, "Remove Sky Replacement")

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
        if not settings.get("source"):
            return
        matte = self._matte()
        if matte is None:
            self._pending = settings
            if not self._busy:
                self._start(settings)
            return
        sky = {**sky_replace.normalized(settings), "mask": matte}
        if not sky_replace.valid(sky):
            self._error = "Sky Replacement could not be applied to this photo."
            self.changed.emit()
            return
        self._error = ""
        self.raw._set_values({"sky": sky}, "Sky Replacement")

    # -- library
    @Slot("QVariant")
    def importSkies(self, locations):
        """Add picture files (paths or file URLs) to the library; the last one is chosen."""
        last = None
        problems = []
        for location in locations or []:
            text = str(location)
            path = QUrl(text).toLocalFile() if text.startswith("file:") else text
            try:
                last = sky_replace.import_sky(path, self._library_root)
            except (OSError, ValueError) as error:
                problems.append(f"{Path(path).name}: {error}")
        self._error = "; ".join(problems[:2])
        self.changed.emit()
        if last is not None:
            self.choose(str(last))

    @Slot(str)
    def forget(self, path):
        """Remove a sky from the library; a photo using it loses the sky too."""
        if sky_replace.forget_sky(path, self._library_root):
            self._thumbs.pop(path, None)
            if self.raw._draft.get("sky", {}).get("source") == path:
                self.remove()
            self.changed.emit()

    # -- the matte job
    def _snapshot(self):
        from .raw_preview import frame_image

        for source in (self.raw._baseline, self.raw._preview):
            if not source:
                continue
            image = frame_image(source) if source.startswith("image://") else QImageReader(source).read()
            if not image.isNull():
                return image.scaled(1600, 1600, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        raise ValueError("Wait for the RAW preview before replacing the sky.")

    def _start(self, settings):
        self.refresh()
        if self._reason:
            self._error, self._pending = self._reason, None
            self.changed.emit()
            return
        try:
            image = self._snapshot()
        except ValueError:
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
        self._busy, self._message, self._error = True, "Finding the sky…", ""
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
                result = (signature, runner(image, progress, cancel))
            except Exception as exc:
                error = str(exc) or "The sky could not be found"
            try:
                self._finished.emit(ticket, result, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="qml-sky-matte", daemon=True).start()

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
            error = "The photo changed while its sky was being found. Try again."
        if error:
            self._error, self._message = error, ""
            self.changed.emit()
            return
        self._mattes[self._path] = result
        self._mattes.move_to_end(self._path)
        while len(self._mattes) > _KEPT_MATTES:
            self._mattes.popitem(last=False)
        self._message = ""
        if pending is not None:
            self._apply(pending)
        self.changed.emit()
