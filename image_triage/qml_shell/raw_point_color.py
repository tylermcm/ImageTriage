"""Point Color in the RAW editor: pick a colour in the photo, then shift that colour's hue, saturation and luminance.

The pixels are PhotoCraft's (`finishing::points`); this is the authoring. A sample's colour is the picture's own encoded
value at the point where Point Color acts, so it is read by the preview helper from a develop that leaves Point Color and
the masks out, not from the display picture.
"""
from __future__ import annotations

import math
from copy import deepcopy

from PySide6.QtCore import QObject, Property, Signal, Slot

MAX_SAMPLES = 8          # the recipe's limit
RANGES = {"hue": (-100, 100), "saturation": (-100, 100), "luminance": (-100, 100), "variance": (-100, 100), "range": (0, 100)}
NEW = {"hue": 0, "saturation": 0, "luminance": 0, "variance": 0, "range": 50}


def swatch(color) -> str:
    """A screen colour for a sample. The stored value is the picture's encoded RGB, close enough to show which colour it is."""
    return "#" + "".join(f"{max(0, min(255, round(float(c) * 255))):02x}" for c in color)


class RawPointColor(QObject):
    changed = Signal()

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._path = ""
        self._selected = -1
        self._picking = False
        raw.changed.connect(self._sync)

    def _sync(self):
        if self._path != self.raw._path:
            self._path, self._selected, self._picking = self.raw._path, -1, False
        count = len(self.raw._draft.get("pointColors", []))
        if self._selected >= count:
            self._selected = count - 1
        self.changed.emit()

    def _rows(self) -> list:
        return deepcopy(self.raw._draft.get("pointColors", []))

    @Property("QVariantList", notify=changed)
    def rows(self):
        return [{**NEW, **row, "swatch": swatch(row.get("color", (0.5, 0.5, 0.5)))} for row in self._rows()]

    selected = Property(int, lambda self: self._selected, notify=changed)
    picking = Property(bool, lambda self: self._picking, notify=changed)
    full = Property(bool, lambda self: len(self.raw._draft.get("pointColors", [])) >= MAX_SAMPLES, notify=changed)

    @Property("QVariantMap", notify=changed)
    def current(self):
        rows = self.rows
        return rows[self._selected] if 0 <= self._selected < len(rows) else {}

    @Slot(bool)
    def setPicking(self, on: bool) -> None:
        """Arm or disarm the picker. It needs the preview helper (the colour is read from the develop) and a free slot."""
        from . import raw_preview

        if on and (not self.raw.loaded or self.raw.saving):
            return
        if on and not raw_preview.executable():
            self.raw._error = "Picking a color needs the RAW preview helper."
            self.raw.changed.emit()
            return
        if on and self.full:
            self.raw._error = f"A photo can have up to {MAX_SAMPLES} Point Color samples. Delete one first."
            self.raw.changed.emit()
            return
        if on != self._picking:
            self._picking = on
            self.changed.emit()

    @Slot(float, float)
    def pick(self, x: float, y: float) -> None:
        """A click on the photo while picking: ``x``, ``y`` are fractions of the picture as shown (inside the crop)."""
        if not self._picking or not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            return
        crop = self.raw.values["hostCrop"]
        if not self.raw.cropEditing:
            x, y = crop["x"] + x * crop["w"], crop["y"] + y * crop["h"]
        self._picking = False
        self.raw._enqueue("maskSample", {"area": [x, y, x, y], "kind": "pointColor", "add": False, "target": -1})
        self.raw.changed.emit()
        self.changed.emit()

    def applySample(self, result, sample) -> None:
        rgb = result["sampleRGB"]
        if len(rgb) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in rgb):
            raise ValueError("The color could not be read from the photo.")
        rows = self._rows()
        if len(rows) >= MAX_SAMPLES:
            raise ValueError(f"A photo can have up to {MAX_SAMPLES} Point Color samples.")
        rows.append({"color": [max(0.0, min(1.0, float(v))) for v in rgb], **NEW})
        self._selected = len(rows) - 1
        self.raw._set_values({"pointColors": rows}, "Point Color sample")

    @Slot(int)
    def select(self, index: int) -> None:
        if -1 <= index < len(self._rows()) and index != self._selected:
            self._selected = index
            self.changed.emit()

    @Slot(str, float)
    def adjust(self, key: str, value: float) -> None:
        rows = self._rows()
        if key not in RANGES or not math.isfinite(value) or not 0 <= self._selected < len(rows):
            return
        low, high = RANGES[key]
        rows[self._selected][key] = max(low, min(high, value))
        self.raw._set_values({"pointColors": rows}, "Point Color " + key)

    @Slot()
    def remove(self) -> None:
        rows = self._rows()
        if 0 <= self._selected < len(rows):
            rows.pop(self._selected)
            self._selected = min(self._selected, len(rows) - 1)
            self.raw._set_values({"pointColors": rows}, "Delete Point Color sample")

    @Slot()
    def removeAll(self) -> None:
        if self._rows():
            self._selected = -1
            self.raw._set_values({"pointColors": []}, "Delete all Point Color samples")
