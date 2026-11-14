"""Bursts among the photos shown, for the viewer and the grid: which burst a photo is in and where the next and previous bursts start.

The grouping is `bursts.burst_runs` on the capture times `PhotoModel` reads on its worker. A photo with no Exif date has only the time
its file was changed, and files written one after another (an export, a copy) would look like one long burst, so such a photo is in no
burst. `PhotoModel` does not say which of its times are real: a time that differs from the file's is real, and one that is equal is
checked by reading the file again, here on a worker, and only for photos that would be in a burst.

QML reaches this as `popout.bursts`.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..bursts import burst_runs
from ..capture_times import capture_or_modified, read_capture_time


def _file_time(row: dict) -> float | None:
    """What `capture_or_modified` gives a photo without an Exif date: its file's time (asked with no path, so no file is read)."""
    record = row.get("record")
    modified = int(getattr(record, "identity_modified_ns", 0) or 0) if record is not None else 0
    return capture_or_modified("", modified) if modified else None


class BurstModel(QObject):
    changed = Signal()
    _checked = Signal(int, object)

    def __init__(self, photos, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._revision = 0
        self._groups: list[tuple[int, ...]] | None = None      # each burst as shown rows in the order taken; the bursts in the order shown
        self._where: dict[int, tuple[int, int]] = {}           # shown row -> (burst, place in it)
        self._real: dict[str, bool] = {}                       # path -> has an Exif date, for the photos whose time equals their file's
        self._asking: set[str] = set()
        self._times_token = 0
        self._checked.connect(self._checked_arrived)
        photos.modelReset.connect(self._invalidate)
        photos.captureTimesChanged.connect(self._times_changed)

    # -- keeping up ---------------------------------------------------------------------------------------------------------
    def _invalidate(self) -> None:
        self._groups = None
        self._revision += 1
        self.changed.emit()

    def _times_changed(self) -> None:
        self._times_token += 1                                  # checks asked for the folder before are not for this one
        self._real, self._asking = {}, set()
        self._invalidate()

    def _checked_arrived(self, token: int, found: dict) -> None:
        if token != self._times_token:
            return
        self._real.update(found)
        self._asking.difference_update(found)
        self._invalidate()

    def _check(self, paths: list[str]) -> None:
        token = self._times_token
        self._asking.update(paths)

        def work() -> None:
            found = {path: read_capture_time(path) is not None for path in paths}
            try:
                self._checked.emit(token, found)
            except RuntimeError:
                pass                                            # the model was destroyed meanwhile

        threading.Thread(target=work, name="qml-burst-times", daemon=True).start()

    def _build(self) -> list[tuple[int, ...]]:
        if self._groups is not None:
            return self._groups
        times = self._photos.capture_times()                    # starts the read when it has not been done for this folder
        rows = [self._photos.row_at(i) for i in range(self._photos.rowCount())]
        taken: list[float | None] = [times.get(row["path"]) for row in rows]
        unsure = [i for i, row in enumerate(rows) if taken[i] is not None and taken[i] == _file_time(row)]
        if unsure:
            # only a photo that would be in a burst is worth reading again
            possible = {i for run in burst_runs(taken) for i in run}
            ask = [rows[i]["path"] for i in unsure if i in possible and rows[i]["path"] not in self._real and rows[i]["path"] not in self._asking]
            if ask:
                self._check(ask)
            for i in unsure:
                if not self._real.get(rows[i]["path"], False):
                    taken[i] = None
        self._groups = sorted(burst_runs(taken), key=lambda run: run[0])
        self._where = {row: (index, place) for index, run in enumerate(self._groups) for place, row in enumerate(run)}
        return self._groups

    # -- what QML and the viewer ask ----------------------------------------------------------------------------------------
    revision = Property(int, lambda self: self._revision, notify=changed)

    @Property(bool, notify=changed)
    def reading(self) -> bool:
        """The capture times are still being read: no burst is known yet, which is not the same as there being none."""
        self._build()
        if self._asking:
            return True
        return not self._photos.capture_times() and any(row.get("record") is not None for row in self._photos.all_rows())

    @Property(int, notify=changed)
    def count(self) -> int:
        return len(self._build())

    def rows_of(self, row: int) -> tuple[int, ...]:
        """The shown rows of the burst the photo on `row` is in, in the order taken; empty when it is in none."""
        groups = self._build()
        found = self._where.get(row)
        return groups[found[0]] if found else ()

    @Slot(int, result="QVariantMap")
    def info(self, row: int) -> dict:
        """{"burst": its number among the bursts shown, "place": this photo's number in it, "size", "label"}, or {} for a photo in no burst."""
        groups = self._build()
        found = self._where.get(row)
        if not found:
            return {}
        size = len(groups[found[0]])
        return {"burst": found[0] + 1, "place": found[1] + 1, "size": size, "label": f"Burst {found[0] + 1} · {found[1] + 1} of {size}"}

    @Slot(int, int, result=int)
    def target(self, row: int, direction: int) -> int:
        """The row of the first photo of the next (direction > 0) or previous burst from `row`, or -1 when there is none that way."""
        groups = self._build()
        own = self._where.get(row, (-1, 0))[0]
        starts = [run[0] for index, run in enumerate(groups) if index != own]
        if direction > 0:
            after = [start for start in starts if start > row]
            return min(after) if after else -1
        before = [start for start in starts if start < row]
        return max(before) if before else -1

    @Slot(int, result=str)
    def missing(self, direction: int) -> str:
        """Why `target` found nothing, in words for a toast."""
        if self.reading:
            return "Still reading when the photos were taken. Try again in a moment."
        if not self._build():
            return "No bursts among the photos shown"
        return "No burst after this photo" if direction > 0 else "No burst before this photo"
