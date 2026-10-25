"""The popout: the Widgets app's own full-screen viewer (`preview.FullScreenPreview`), opened from the QML shell's grid.

The viewer is kept as it is, a separate Widgets window. This host does what `preview_controller.PreviewController` does for
`window.MainWindow` and nothing more: it builds the viewer once, hands it the photos the grid shows (`PreviewEntry`), steps through
them, preloads the neighbours, feeds its filmstrip from the shell's thumbnail cache and writes the decisions and ratings made in the
viewer back to the photo model. The editor never starts from here (`set_photocraft_available(False)`: that is the next step in
docs/qml_unwired.md), so browsing stays the instant viewer alone.

Like the Widgets app, the grid is not told about every step: it moves to the photo the viewer ended on when the viewer is put away.
"""
from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

from ..app_identity import user_settings
from ..models import ImageRecord
from ..preload_order import candidate_indexes

FILMSTRIP_BUCKET = 192        # the strip's thumbnails are 192 x 128 (`window.MainWindow.PREVIEW_FILMSTRIP_THUMB_SIZE`), the shell's are 3:2
PRELOAD_INTERVAL_MS = 120     # as in window.py: preloading is throttled, so a held arrow key still preloads while it is held
WINNER_TAG = "#ee719e"        # the strip's status dots, as `PreviewController.preview_filmstrip_tag`
REJECT_TAG = "#ff6b6b"        # `ui.preview_studio.REJECT`, repeated so that importing this module does not import the viewer
THUMB_CACHE = 512             # filmstrip pixmaps kept by path (the Widgets controller keeps the same number)


class PopoutHost(QObject):
    openChanged = Signal()
    closed = Signal(int)      # the viewer was put away on this row of the grid

    def __init__(self, photos, thumbs, collections=None, *, prebuild: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._thumbs = thumbs
        self._collections = collections
        self._window = None                                  # the main window, to open the viewer on its screen
        self._preview = None
        self._open = False
        self._row = -1
        self._travel = 0                                     # +1 / -1 after stepping forward / back: biases what is preloaded
        self._compare = False
        self._compare_count = 3
        self._pixmaps: OrderedDict[str, QPixmap] = OrderedDict()
        self._awaiting: set[str] = set()                     # filmstrip thumbnails asked for and not yet in the cache
        self._preload_row: int | None = None
        self._preload_timer = QTimer(self)
        self._preload_timer.setSingleShot(True)
        self._preload_timer.setInterval(PRELOAD_INTERVAL_MS)
        self._preload_timer.timeout.connect(self._run_preload)
        thumbs.ready.connect(self._thumbnail_ready)
        if prebuild:
            # Built after the window is up, as the Widgets app does (`run_deferred_preview_build`), so the first Space is not slow.
            QTimer.singleShot(1500, self._prebuild)

    # -- what QML sees ----------------------------------------------------------------------------------------------------
    isOpen = Property(bool, lambda self: self._open, notify=openChanged)

    def setWindow(self, window) -> None:
        self._window = window

    def preview(self):
        """The viewer, built if it is not yet (tests and the editor step use it)."""
        return self._ensure()

    @Slot(int)
    def open(self, row: int) -> None:
        """Show the grid's photo `row` in the viewer (Enter, Space, double click)."""
        if not 0 <= row < self._photos.rowCount():
            return
        self._ensure()
        self._row = row
        self._travel = 0
        self._show(row)

    @Slot()
    def close(self) -> None:
        if self._preview is not None and self._preview.isVisible():
            self._preview.close()

    def shutdown(self) -> None:
        """The application is quitting: really close the viewer (it only hides when closed)."""
        self._preload_timer.stop()
        preview, self._preview = self._preview, None
        if preview is None:
            return
        try:
            preview.closed.disconnect(self._viewer_closed)
        except (RuntimeError, TypeError):
            pass
        preview.hide_on_close = False
        preview.close()
        preview.deleteLater()

    # -- building the viewer ----------------------------------------------------------------------------------------------
    def _prebuild(self) -> None:
        if self._preview is None:
            self._ensure()

    def _ensure(self):
        if self._preview is not None:
            return self._preview
        from ..preview import FullScreenPreview          # a heavy import: only when the viewer is first wanted

        settings = user_settings()
        preview = FullScreenPreview()
        preview.navigation_requested.connect(self._navigate)
        preview.compare_mode_changed.connect(self._compare_mode_changed)
        preview.compare_count_changed.connect(self._compare_count_changed)
        preview.winner_requested.connect(lambda path: self._decide(path, "win"))
        preview.reject_requested.connect(lambda path: self._decide(path, "rej"))
        preview.keep_requested.connect(lambda path: self._decide(path, "keep"))
        preview.rating_requested.connect(self._rate)
        preview.collect_requested.connect(self._collect)
        preview.closed.connect(self._viewer_closed)
        preview.set_photoshop_available(False)
        preview.set_photocraft_available(False)           # no editor from here yet: the key does nothing and browsing is the viewer alone
        preview.set_auto_advance_enabled(settings.value("view/auto_advance", True, bool))
        preview.set_preload_batch_size(settings.value("preview/preload_batch_size", FullScreenPreview.DEFAULT_PRELOAD_BATCH_SIZE, int))
        self._preview = preview
        return preview

    # -- opening and stepping ---------------------------------------------------------------------------------------------
    def _show(self, row: int) -> None:
        entries = self._entries_for(row)
        if not entries:
            return
        preview = self._preview
        if self._compare:
            preview.set_compare_count(len(entries))
        preview.set_winner_ladder_mode(False)
        if not preview.isVisible() and self._window is not None:
            preview.setGeometry(self._window.screen().geometry())           # open on the screen the grid is on
        preview.show_entries(entries)
        self._browse_context(row)
        self._schedule_preload(row)
        self._set_open(True)

    def _entries_for(self, row: int) -> list:
        if not self._compare:
            entry = self._entry(row)
            return [entry] if entry is not None else []
        end = min(self._photos.rowCount(), row + max(1, self._compare_count))
        return [entry for entry in (self._entry(index) for index in range(row, end)) if entry is not None]

    def _entry(self, row: int):
        from ..preview import PreviewEntry

        item = self._photos.row_at(row)
        if item is None:
            return None
        return PreviewEntry(
            record=self._record(item),
            source_path=item["path"],
            winner=item["decision"] == "win",
            reject=item["decision"] == "rej",
            rating=item["rating"],
            placeholder_image=self._placeholder(item["path"]),
        )

    @staticmethod
    def _record(item: dict) -> ImageRecord:
        record = item.get("record")
        if record is not None:
            return record
        try:
            stat = os.stat(item["path"])
            size, modified = stat.st_size, stat.st_mtime_ns
        except OSError:
            size, modified = 0, 0
        return ImageRecord(path=item["path"], name=Path(item["path"]).name, size=size, modified_ns=modified,
                           companion_paths=tuple(item.get("companions", ())))

    def _placeholder(self, path: str):
        """The grid's thumbnail of the photo, drawn at once while the real picture is decoded (None when there is none yet)."""
        file = self._thumbs.cached_file(path, FILMSTRIP_BUCKET)
        if not file:
            return None
        image = QImage(file)
        return None if image.isNull() else image

    def _navigate(self, delta: int) -> None:
        count = self._photos.rowCount()
        if count == 0 or self._row < 0:
            return
        target = max(0, min(count - 1, self._row + delta))
        if delta:
            self._travel = 1 if delta > 0 else -1
            if target == self._row:
                return                                          # at the first or last photo: nothing to show
        self._row = target
        self._show(target)

    # -- compare ----------------------------------------------------------------------------------------------------------
    def _compare_mode_changed(self, enabled: bool) -> None:
        self._compare = bool(enabled)
        self._preview.set_compare_mode(self._compare)
        if self._row >= 0 and self._preview.isVisible():
            self._show(self._row)

    def _compare_count_changed(self, count: int) -> None:
        self._compare_count = int(count)
        if self._compare and self._row >= 0 and self._preview.isVisible():
            self._show(self._row)

    # -- decisions and ratings made in the viewer -------------------------------------------------------------------------
    def _decide(self, path: str, decision: str) -> None:
        row = self._photos.row_for_path(path)
        if row < 0:
            return
        self._photos.setDecision(row, decision)                   # a toggle, as the grid's own keys
        if decision == "win" and self._collections is not None and self._collections.activeId != "":
            self._collections.addSelection(self._collections.activeId, row)     # "New winners go here"
        self._annotated(row)

    def _rate(self, path: str, rating: int) -> None:
        row = self._photos.row_for_path(path)
        item = self._photos.row_at(row)
        if item is None or item["rating"] == max(0, min(5, int(rating))):
            return
        self._photos.setRating(row, max(0, min(5, int(rating))))
        self._annotated(row)

    def _collect(self, path: str) -> None:
        row = self._photos.row_for_path(path)
        if row >= 0 and self._collections is not None and self._collections.activeId != "":
            self._collections.addSelection(self._collections.activeId, row)

    def _annotated(self, row: int) -> None:
        item = self._photos.row_at(row)
        if item is None or self._preview is None:
            return
        self._preview.set_annotation_state(item["path"], item["decision"] == "win", item["decision"] == "rej", item["rating"])
        self._preview.refresh_filmstrip()

    # -- the filmstrip and the preload ------------------------------------------------------------------------------------
    def _browse_context(self, row: int) -> None:
        self._preview.set_browse_context(self._photos.rowCount(), row, self._strip_thumb, self._strip_tag, self._strip_marked)

    def _strip_thumb(self, position: int):
        item = self._photos.row_at(position)
        return None if item is None else self._pixmap(item["path"])

    def _strip_tag(self, position: int):
        item = self._photos.row_at(position)
        if item is None:
            return None
        return REJECT_TAG if item["decision"] == "rej" else WINNER_TAG if item["decision"] == "win" else None

    @staticmethod
    def _strip_marked(position: int) -> bool:
        return False                                              # members of the active collection: not drawn yet

    def _pixmap(self, path: str):
        pixmap = self._pixmaps.get(path)
        if pixmap is not None:
            self._pixmaps.move_to_end(path)
            return pixmap
        file = self._thumbs.cached_file(path, FILMSTRIP_BUCKET)
        if file:
            pixmap = QPixmap(file)
            if pixmap.isNull():
                return None
            self._pixmaps[path] = pixmap
            if len(self._pixmaps) > THUMB_CACHE:
                self._pixmaps.popitem(last=False)
            self._no_longer_awaited(path)
            return pixmap
        if path not in self._awaiting:
            self._awaiting.add(path)
            self._thumbs.url(path, FILMSTRIP_BUCKET)              # queues it; it counts as wanted until it is in the cache
        return None

    def _no_longer_awaited(self, path: str) -> None:
        if path in self._awaiting:
            self._awaiting.discard(path)
            self._thumbs.unwant(path)

    def _thumbnail_ready(self, path: str) -> None:
        if path in self._awaiting and self._preview is not None:
            self._preview.refresh_filmstrip()

    def _schedule_preload(self, row: int) -> None:
        self._preload_row = row
        if not self._preload_timer.isActive():
            self._preload_timer.start()

    def _run_preload(self) -> None:
        row, self._preload_row = self._preload_row, None
        preview = self._preview
        if row is None or preview is None or not preview.isVisible():
            return
        limit = preview.preload_batch_size()
        if limit <= 0:
            return
        paths: list[str] = []
        for index in candidate_indexes(row, self._photos.rowCount(), self._travel):
            if len(paths) >= limit:
                break
            item = self._photos.row_at(index)
            if item is not None:
                paths.append(item["path"])
        preview.preload_paths(paths)

    # -- closing ----------------------------------------------------------------------------------------------------------
    def _viewer_closed(self) -> None:
        self._preload_timer.stop()
        self._set_open(False)
        self.closed.emit(self._row)

    def _set_open(self, value: bool) -> None:
        if value != self._open:
            self._open = value
            self.openChanged.emit()
