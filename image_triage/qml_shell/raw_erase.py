"""AI Erase in the editor: click an object, grow the selection, fill it. The fill becomes one ``patch`` retouch stroke (see
``image_triage.erase``); nothing is baked into the photo."""
from __future__ import annotations

import math
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import Property, QObject, QRect, Signal, Slot
from PySide6.QtGui import QImage

from .. import erase
from ..raw_retouch import valid


def image_to_array(image: QImage):
    import numpy as np

    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    stride = rgb.bytesPerLine()
    data = np.frombuffer(rgb.constBits(), np.uint8, count=stride * rgb.height()).reshape(rgb.height(), stride)
    return np.array(data[:, : rgb.width() * 3].reshape(rgb.height(), rgb.width(), 3), copy=True)     # a copy: the buffer dies with ``rgb``


def overlay_image(mask, crop) -> QImage:
    """A translucent tint over the selection, cut to the part of the frame the editor shows (``crop`` in frame fractions)."""
    import numpy as np

    h, w = mask.shape
    x0, y0 = int(crop["x"] * w), int(crop["y"] * h)
    x1, y1 = max(x0 + 1, int((crop["x"] + crop["w"]) * w)), max(y0 + 1, int((crop["y"] + crop["h"]) * h))
    part = mask[y0:y1, x0:x1] > 127
    rgba = np.zeros(part.shape + (4,), np.uint8)
    rgba[part] = (40, 130, 255, 120)
    rgba = np.ascontiguousarray(rgba)
    return QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.shape[1] * 4, QImage.Format.Format_RGBA8888).copy()


class RawErase(QObject):
    changed = Signal()
    _done = Signal(int, object, str)       # serial, result, error

    def __init__(self, raw) -> None:
        super().__init__(raw)
        self.raw = raw
        self._serial = 0
        self._busy = False
        self._message = ""
        self._error = ""
        self._grow = 0.0
        self._source = None            # uint8 RGB, the picture the selection was made on
        self._mask = None              # uint8, 255 = object, same size
        self._overlay = ""
        self._click = None
        self._cancel = threading.Event()
        self._path = ""
        self._done.connect(self._finished)
        raw.changed.connect(self._sync)

    # --- state ---------------------------------------------------------------------------------------------------------
    def _sync(self) -> None:
        if self._path != self.raw._path:
            self._path = self.raw._path
            self.clearSelection()

    def _size(self):
        size = self.raw._full_size
        return (size[0], size[1]) if size and min(size) > 0 else (4000, 3000)

    busy = Property(bool, lambda self: self._busy, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    overlay = Property(str, lambda self: self._overlay, notify=changed)
    hasSelection = Property(bool, lambda self: self._mask is not None, notify=changed)
    grow = Property(float, lambda self: self._grow or float(erase.default_grow(self._size())), notify=changed)
    maxGrow = Property(float, lambda self: float(erase.max_grow(self._size())), notify=changed)
    available = Property(bool, lambda self: self._available(), notify=changed)

    @staticmethod
    def _available() -> bool:
        try:
            from ..ai_model import lama_installation

            return lama_installation().is_installed
        except Exception:
            return False

    @Slot(float)
    def setGrow(self, value: float) -> None:
        if math.isfinite(value):
            self._grow = max(0.0, min(float(erase.max_grow(self._size())), value))
            self.changed.emit()

    @Slot()
    def clearSelection(self) -> None:
        self._cancel.set()
        self._serial += 1
        self._source = self._mask = self._click = None
        self._busy = False
        self._message = self._error = ""
        self._discard()
        self.changed.emit()

    def _discard(self) -> None:
        if self._overlay:
            from . import raw_preview

            raw_preview.discard_frame(self._overlay)
            self._overlay = ""

    # --- selecting -----------------------------------------------------------------------------------------------------
    @Slot(float, float)
    def select(self, x: float, y: float) -> None:
        """Select the object at (x, y), as fractions of what the editor shows."""
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)) or self._busy:
            return
        if not self.raw.loaded or self.raw.saving:
            return
        from . import raw_preview

        if not raw_preview.executable():
            self._error = "AI Erase needs the RAW preview worker."
            self.changed.emit()
            return
        crop = self.raw.values["hostCrop"]
        self._click = [crop["x"] + x * crop["w"], crop["y"] + y * crop["h"]]
        self._busy, self._error, self._message = True, "", "Rendering the photo..."
        self._cancel = threading.Event()
        self._serial += 1
        self.raw._enqueue("maskSample", {"kind": "erase", "serial": self._serial})
        self.changed.emit()

    def accept_source(self, image: QImage, sample: dict) -> None:
        """The preview worker's render of the photo as the retouch stage sees it."""
        if sample.get("serial") != self._serial or self._click is None:
            return
        serial, click, cancel = self._serial, list(self._click), self._cancel
        source = image_to_array(image)
        self._message = "Selecting the object..."
        self.changed.emit()
        threading.Thread(target=self._select_job, args=(serial, source, click, cancel), daemon=True).start()

    def _select_job(self, serial, source, click, cancel) -> None:
        try:
            import numpy as np
            from PIL import Image

            from ..prompt_masks import ensure_prompt_mask

            with tempfile.TemporaryDirectory(prefix="erase-") as folder:
                path = Path(folder) / "source.png"
                Image.fromarray(source).save(path)
                result = ensure_prompt_mask(path, points_norm=[click], labels=[1], progress_callback=None,
                                            cache_root=Path(folder) / "sam")
                with Image.open(result.mask_path) as opened:
                    mask = np.asarray(opened.convert("L").resize((source.shape[1], source.shape[0]), Image.Resampling.BILINEAR))
            if float((mask > 127).mean()) < 0.00005:
                raise ValueError("Nothing was found there. Click on the object.")
            self._done.emit(serial, ("select", source, mask), "")
        except Exception as error:  # reported in the panel
            self._done.emit(serial, None, str(error) or type(error).__name__)

    # --- filling -------------------------------------------------------------------------------------------------------
    @Slot()
    def erase(self) -> None:
        if self._busy or self._mask is None or self._source is None or not self.raw.loaded or self.raw.saving:
            return
        self._busy, self._error, self._message = True, "", "Filling in..."
        self._serial += 1
        serial, source, mask, cancel = self._serial, self._source, self._mask, self._cancel
        grow, photo = self.grow, self.raw._path
        self.changed.emit()
        threading.Thread(target=self._fill_job, args=(serial, source, mask, grow, photo, cancel), daemon=True).start()

    def _fill_job(self, serial, source, mask, grow, photo, cancel) -> None:
        try:
            import numpy as np
            from PIL import Image

            from ..scene_mask_jobs import run_inpaint

            with tempfile.TemporaryDirectory(prefix="erase-") as folder:
                work = Path(folder)

                def fill(crop, hole):
                    Image.fromarray(crop).save(work / "crop.png")
                    Image.fromarray(hole).save(work / "hole.png")
                    run_inpaint(work / "crop.png", work / "hole.png", work / "out.png", None, cancel)
                    return np.asarray(Image.open(work / "out.png").convert("RGB"))

                stroke, info = erase.make_patch(source, mask, fill, grow=grow, photo_path=photo)
            self._done.emit(serial, ("fill", stroke), "")
        except Exception as error:
            self._done.emit(serial, None, str(error) or type(error).__name__)

    def _finished(self, serial: int, result, error: str) -> None:
        if serial != self._serial:
            return
        self._busy = False
        self._message = ""
        if error:
            self._error = error
        elif result[0] == "select":
            self._source, self._mask = result[1], result[2]
            self._publish_overlay()
        elif result[0] == "fill":
            rows = list(self.raw._draft.get("retouch", [])) + [result[1]]
            if not valid(rows):
                self._error = "Retouch stroke limit reached. Undo or clear strokes before adding more."
            else:
                self._source = self._mask = None
                self._discard()
                self.changed.emit()
                self.raw._set_values({"retouch": rows}, "AI Erase")
                return
        self.changed.emit()

    def _publish_overlay(self) -> None:
        from . import raw_preview

        self._discard()
        if self._mask is not None:
            self._overlay = raw_preview.put_frame(f"{self.raw._owner}/erase/{self._serial}", overlay_image(self._mask, self.raw.values["hostCrop"]))
