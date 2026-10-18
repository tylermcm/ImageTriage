"""Native ACR filmstrip adapter. The host retains browse order, thumbnails and navigation."""
from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage

from .photocraft_bridge import PhotoCraftControl
from .models import FilterMode, SortMode
from .scanner import normalized_path_key

_log = logging.getLogger(__name__)


class NativeFilmstrip(QObject):
    """One bounded visible window; all QPixmap access stays on the Qt thread.

    A separate control connection keeps strip requests out of the photo-load/save
    queue. Native requests identify both the browse generation and stable photo,
    so a click cannot select a different photo after sorting or filtering.
    """

    _replied = Signal(object, int, object, object)

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.proc = None
        self.records = ()
        self.identities = ()
        self.generation = 1
        self.first = 0
        self.last = 16
        self.ack = 0
        self.sent = {}
        self.future = None
        self._last_error = None
        self.closed = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="photocraft-filmstrip")
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self._replied.connect(self._apply)

    def refresh_source(self):
        window = self.controller._window
        if getattr(window, "_scan_in_progress", False):
            return  # A rescan temporarily clears _records; retain the committed browse window.
        self.records = tuple(record for record in getattr(window, "_records", ()) if not getattr(record, "is_folder", False))
        identities = tuple(normalized_path_key(record.path) for record in self.records)
        if identities != self.identities:
            self.identities = identities
            self.generation += 1
            self.first, self.last = 0, 16
            self.ack = 0
            self.sent.clear()

    def attach(self, proc):
        if self.closed or getattr(proc.control, "filmstrip_supported", False) is not True:
            return
        self.refresh_source()
        if not self.records:
            return
        if self.proc is not proc:
            self.sent.clear()
        self.proc = proc
        self.timer.start()
        self._tick()

    def _current(self):
        preview = self.controller.preview_if_built()
        entries = getattr(preview, "_source_entries", ())
        path = normalized_path_key(entries[0].record.path) if entries else ""
        try:
            return self.identities.index(path)
        except ValueError:
            return 0

    def _packet(self):
        self.refresh_source()
        current = self._current()
        first = min(self.first, max(0, len(self.records) - 1))
        last = min(len(self.records), max(first + 1, self.last), first + 32)
        positions = list(range(first, min(last, first + 31)))
        if self.records and current not in positions:
            positions.append(current)
        visible_ids = {self.identities[position] for position in positions}
        self.sent = {identity: version for identity, version in self.sent.items() if identity in visible_ids}
        window = self.controller._window
        annotations = getattr(window, "_annotations", {})
        indexes = {normalized_path_key(record.path): index for index, record in enumerate(getattr(window, "_records", ()))}
        items, uploaded = [], None
        for position in positions:
            record = self.records[position]
            identity = self.identities[position]
            annotation = annotations.get(record.path)
            item = {"index": position, "id": identity, "name": Path(record.path).name,
                    "rating": int(getattr(annotation, "rating", 0)), "rejected": bool(getattr(annotation, "reject", False)),
                    "edited": any(self.controller._filmstrip_edit_state(record.path))}
            host_index = indexes.get(identity)
            pixmap = self.controller.preview_filmstrip_thumb(host_index) if host_index is not None else None
            if pixmap is not None and not pixmap.isNull():
                version = str(pixmap.cacheKey())
                item["version"] = version
                if uploaded is None and self.sent.get(identity) != version:
                    image = pixmap.toImage().scaled(384, 384, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
                    rgba = bytes(image.constBits())
                    if len(rgba) != image.width() * image.height() * 4:
                        raise ValueError("Filmstrip RGBA thumbnail has unexpected row padding")
                    item["thumbnail"] = {"width": image.width(), "height": image.height(), "rgba": base64.b64encode(rgba).decode("ascii")}
                    uploaded = (identity, version)
            items.append(item)
        preview = self.controller.preview_if_built()
        enabled = bool(getattr(preview, "_native_filmstrip_active", False))
        return {"generation": self.generation, "total": len(self.records), "current": current, "items": items, "enabled": enabled, "ack": self.ack}, uploaded

    def _tick(self):
        preview = self.controller.preview_if_built()
        if self.closed or self.proc is None or preview is None:
            return
        if not self.proc.is_running():
            self.timer.stop()
            preview.set_native_filmstrip(False)
            return
        if not preview.isVisible():
            return
        if getattr(self.controller._window, "_scan_in_progress", False):
            return
        if self.future is not None and not self.future.done():
            return
        try:
            packet, uploaded = self._packet()
        except Exception:
            _log.exception("Could not prepare native filmstrip window; retaining the current presentation")
            return
        proc, generation = self.proc, self.generation
        self.future = self.executor.submit(self._exchange, proc, packet)

        def finished(future):
            try:
                reply = future.result()
            except Exception as error:
                reply = error
            try:
                self._replied.emit(proc, generation, reply, uploaded)
            except RuntimeError:
                pass  # The controller has already been destroyed.

        self.future.add_done_callback(finished)

    def _exchange(self, proc, packet):
        # Connections are short-lived so stopping this adapter never leaves a socket
        # competing with the host's shutdown/save connections.
        control = PhotoCraftControl(proc.port, proc.token, timeout=2.0)
        try:
            return control.call("ui.filmstrip", packet)
        finally:
            control.close()

    def _apply(self, proc, generation, reply, uploaded):
        self.refresh_source()
        if self.closed or proc is not self.proc or generation != self.generation:
            return
        if getattr(self.controller._window, "_scan_in_progress", False):
            return  # Revalidate pending native actions against the completed order.
        preview = self.controller.preview_if_built()
        if preview is None:
            return
        if isinstance(reply, Exception):
            # A lost reply does not prove the native strip stopped drawing. Keep
            # the last acknowledged presentation and retry; restoring Qt here
            # could display both strips. Process exit restores the retained strip.
            message = str(reply)
            if message != self._last_error:
                _log.warning("Native filmstrip connection interrupted; retrying: %s", reply)
                self._last_error = message
            return
        if reply.get("generation") != generation:
            return
        self._last_error = None
        if uploaded:
            self.sent[uploaded[0]] = uploaded[1]
        self.first = max(0, int(reply.get("first", 0)))
        self.last = max(self.first + 1, min(self.first + 32, int(reply.get("last", 16))))
        preview.set_native_filmstrip(bool(self.records and reply.get("ready") and reply.get("cameraRawOpen")))
        if getattr(preview, "_native_filmstrip_active", False) and not reply.get("enabled"):
            # Finish visibility negotiation on the next Qt turn; don't leave a
            # timer interval between detaching Qt and enabling the native tray.
            QTimer.singleShot(0, self._tick)
        request = reply.get("request")
        if not isinstance(request, dict) or request.get("generation") != generation:
            return
        serial, index = request.get("serial"), request.get("index")
        if type(serial) is not int or serial <= self.ack or type(index) is not int or not 0 <= index < len(self.identities):
            return
        if request.get("id") != self.identities[index]:
            return
        kind, value = request.get("kind"), request.get("value")
        if kind == "rating" and (type(value) is not int or not 0 <= value <= 5):
            return
        if kind == "navigate" and (type(value) is not int or not 0 <= value < len(self.records)):
            return
        if kind == "sort" and (not isinstance(value, str) or value not in {mode.value for mode in SortMode}):
            return
        if kind == "filter" and (not isinstance(value, str) or value not in {mode.value for mode in FilterMode}):
            return
        if kind not in {"select", "navigate", "rating", "reject", "sort", "filter"}:
            return
        self.ack = serial
        path = self.records[index].path
        if kind == "select":
            preview._handle_studio_filmstrip_selected(index)
        elif kind == "navigate":
            preview._handle_studio_filmstrip_selected(value)
        elif kind == "rating":
            self.controller.handle_preview_rating_requested(path, value)
        elif kind == "reject":
            self.controller.handle_preview_reject_requested(path)
        elif kind == "sort":
            self.controller._window._views.set_sort_mode(SortMode(value))
        elif kind == "filter":
            self.controller._window._records_view.set_filter_mode(FilterMode(value))

    def stop(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        if self.future is not None:
            self.future.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
