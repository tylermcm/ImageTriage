"""Author pupil corrections; PhotoCraft owns the pixel correction algorithm."""
from copy import deepcopy
import math

from PySide6.QtCore import QObject, Property, Signal, Slot

from ..raw_red_eye import valid


class RawRedEye(QObject):
    changed = Signal()

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._path = ""
        self._selected = -1
        self._pupil_size = 50.0
        self._darken = 50.0
        raw.changed.connect(self._sync)

    def _sync(self):
        if self._path != self.raw._path:
            self._path = self.raw._path
            self._selected = -1
        if self._selected >= len(self.rows):
            self._selected = -1
        self.changed.emit()

    def _value(self, key, default):
        rows = self.rows
        return rows[self._selected][key] if 0 <= self._selected < len(rows) else default

    rows = Property("QVariantList", lambda self: deepcopy(self.raw._draft.get("redEye", [])), notify=changed)
    selected = Property(int, lambda self: self._selected, notify=changed)
    pupilSize = Property(float, lambda self: self._value("pupilSize", self._pupil_size), notify=changed)
    darken = Property(float, lambda self: self._value("darken", self._darken), notify=changed)

    @Slot(int)
    def select(self, index):
        if -1 <= index < len(self.rows):
            if index == -1:
                self._pupil_size = self.pupilSize
                self._darken = self.darken
            self._selected = index
            self.changed.emit()

    @Slot(str, float)
    def option(self, key, value):
        if key not in ("pupilSize", "darken") or not math.isfinite(value) or self.raw.saving:
            return
        value = max(1 if key == "pupilSize" else 0, min(100, value))
        setattr(self, "_pupil_size" if key == "pupilSize" else "_darken", value)
        rows = self.rows
        if 0 <= self._selected < len(rows):
            rows[self._selected][key] = value
            self.raw._set_values({"redEye": rows}, "Red Eye " + ("pupil size" if key == "pupilSize" else "darken"))
        else:
            self.changed.emit()

    @Slot(float, float)
    def correct(self, x, y):
        if not self.raw.loaded or self.raw.saving or self.raw.busy:
            return
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            return
        crop = self.raw.values["hostCrop"]
        item = {"point": [crop["x"] + x*crop["w"], crop["y"] + y*crop["h"]],
                "pupilSize": self.pupilSize, "darken": self.darken}
        rows = self.rows + [item]
        if not valid(rows):
            self.raw._error = "Red Eye supports up to 64 corrections. Delete one before adding more."
            self.raw.changed.emit()
            return
        self._selected = len(rows) - 1
        self.raw._set_values({"redEye": rows}, "Red Eye correction")

    @Slot(int)
    def remove(self, index):
        rows = self.rows
        if self.raw.saving or not 0 <= index < len(rows):
            return
        rows.pop(index)
        self._selected = -1
        self.raw._set_values({"redEye": rows}, "Delete Red Eye correction")

    @Slot()
    def clear(self):
        if not self.raw.saving:
            self._selected = -1
            self.raw._set_values({"redEye": []}, "Clear Red Eye corrections")
