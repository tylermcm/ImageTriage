"""The Sky Replacement section of the RAW editor: pick a sky picture, find the photo's sky once, and set how it blends.

The pixels are replaced by PhotoCraft's renderer (the recipe's ``sky`` setting, see ``image_triage.sky_replace``). This
controller finds the sky matte with the landscape model (and, from the same run, the water and the people, for Reflection and
the relight), keeps them for the photo, and writes them plus the sliders into the draft as one undoable setting, throttled like
Light Depth so a slider drag does not queue a render per mouse event.
"""
from __future__ import annotations

import math
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Property, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QImageReader

from .. import editor_tools, photocraft_bridge as bridge, sky_replace

TOOL = "sceneMasks"
_KEPT_MATTES = 6
COMMIT_MS = 80


def _rgb(image):
    """``image`` (a QImage) as float 0..1, h x w x 3."""
    import numpy as np

    from PySide6.QtGui import QImage

    converted = image.convertToFormat(QImage.Format_RGB888)
    rows = np.frombuffer(converted.constBits(), dtype=np.uint8).reshape(converted.height(), converted.bytesPerLine())
    return rows[:, :converted.width() * 3].reshape(converted.height(), converted.width(), 3).astype(np.float32) / 255


SCENE_LABELS = {"sky": "Sky", "water": "Water", "people": "People"}


def run_sky_matte(image, on_progress, cancel):
    """``{"mask", "water", "people"}`` for ``image``, the developed preview: the sky matte (white = sky) and the water and the people
    as raster masks, each snapped to the photo's edges (``None`` when the photo has none). Runs on a worker thread."""
    import numpy as np
    from PIL import Image

    from ..scene_mask_jobs import Cancelled, run_scene_masks
    from .raw_masks import encode_raster

    with tempfile.TemporaryDirectory(prefix="image-triage-sky-") as scratch:
        root = Path(scratch)
        source = root / "source.png"
        if not image.save(str(source), "PNG"):
            raise OSError("Could not prepare the photo for sky detection")
        payload = run_scene_masks(source, root / "manifest.json", on_progress, cancel, labels=SCENE_LABELS)
        if cancel.is_set():
            raise Cancelled()
        rgb = None
        found = {"mask": None, "water": None, "people": None}
        for item in payload.get("categories", []):
            key = {"sky": "mask", "water": "water", "people": "people"}.get(item.get("id"))
            if key is None:
                continue
            path = (root / item["file"]).resolve()
            if path.parent != root.resolve():
                raise ValueError("Unexpected model mask path")
            with Image.open(path) as opened:
                if abs(opened.width / opened.height / (image.width() / image.height()) - 1) > .025:
                    raise ValueError("The model mask does not match the photo's proportions")
                picture = opened.convert("L")
                if float((np.asarray(picture) >= 128).mean()) < .005:
                    continue
                # The model's mask is soft and at a reduced size. Bring it to the photo's size and snap it to the photo's edges.
                picture = picture.resize((image.width(), image.height()), Image.Resampling.BICUBIC)
            rgb = _rgb(image) if rgb is None else rgb
            matte = sky_replace.refine_matte(np.asarray(picture, dtype=np.float32) / 255, rgb)
            refined = Image.fromarray(np.clip(np.rint(matte * 255), 0, 255).astype(np.uint8))
            refined.thumbnail((sky_replace.MATTE_SIDE, sky_replace.MATTE_SIDE), Image.Resampling.BOX)
            found[key] = encode_raster(np.asarray(refined, dtype=np.uint8))
        if found["mask"] is None:
            raise ValueError("No sky found in this photo.")
        return found


class RawSky(QObject):
    changed = Signal()
    _finished = Signal(int, object, str)
    _progress = Signal(int, str)

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._path = ""
        self._mattes = OrderedDict()   # photo path -> (file signature, {"mask", "water", "people"} as the scene job found them)
        self._busy = self._waiting = False
        self._message = self._error = self._reason = ""
        self._ticket = 0
        self._cancel = threading.Event()
        self._pending = None           # settings to apply when the matte arrives
        self._queued = None            # the latest settings asked for, committed by the timer
        self._runner = run_sky_matte
        self._library_root = None      # tests point this somewhere else
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

    @Property(bool, notify=changed)
    def noWater(self):
        """The scene job looked and found no water: Reflection has nothing to reflect in."""
        maps = self._maps() or {}
        return "water" in maps and maps["water"] is None

    @Property(bool, notify=changed)
    def noPeople(self):
        maps = self._maps() or {}
        return "people" in maps and maps["people"] is None

    @Property("QVariantMap", notify=changed)
    def settings(self):
        """The sliders (never the matte: it is large and the panel has no use for it)."""
        current = self._queued if self._queued is not None else self.raw._draft.get("sky")
        return {k: v for k, v in sky_replace.normalized(current).items() if k not in ("mask", *sky_replace.EXTRA_MASKS)}

    @Property("QVariantList", notify=changed)
    def skies(self):
        """The library, each with the file to show as its card. The card loads the picture file itself (scaled down by the
        decoder): pictures kept in the editor's thumbnail cache were pushed out of it by every other thumbnail."""
        sky_replace.seed_bundled(self._library_root)
        chosen = self.settings["source"]
        return [{**item, "thumb": QUrl.fromLocalFile(item["path"]).toString(), "chosen": item["path"] == chosen}
                for item in sky_replace.library(self._library_root)]

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

    def _maps(self):
        """The photo's matte and the other pictures known for it: what the scene job found this session, or else what the saved
        edit holds (a key is missing when nobody has looked for that picture yet; it is None when the job found none)."""
        kept = self._mattes.get(self._path)
        if kept is not None:
            if kept[0] != bridge.file_signature(self._path):
                del self._mattes[self._path]
            else:
                return kept[1]
        sky = self.raw._draft.get("sky")
        if not isinstance(sky, dict) or sky.get("mask") is None:
            return None
        return {key: sky[key] for key in ("mask", *sky_replace.EXTRA_MASKS) if sky.get(key) is not None}

    def _matte(self):
        maps = self._maps()
        return maps.get("mask") if maps else None

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
        maps = self._maps()
        missing = maps is None or any(key not in maps for key in sky_replace.masks_wanted(settings))
        if missing:
            self._pending = settings          # the scene job finds the sky (and the water and people a setting needs)
            if not self._busy:
                self._start(settings)
            return
        sky = sky_replace.for_recipe({**sky_replace.normalized(settings), **{k: v for k, v in maps.items() if v is not None}})
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
        found = result[1] if isinstance(result[1], dict) and "mask" in result[1] else {"mask": result[1]}
        # What the job did not find it looked for: a key it leaves out is None, so a setting wanting it does not run the job again.
        found = {key: found.get(key) for key in ("mask", *sky_replace.EXTRA_MASKS)}
        self._mattes[self._path] = (result[0], found)
        self._mattes.move_to_end(self._path)
        while len(self._mattes) > _KEPT_MATTES:
            self._mattes.popitem(last=False)
        self._message = ""
        if pending is not None:
            self._apply(pending)
        self.changed.emit()
