"""Bounded Camera Raw filmstrip transport over the QML preview's photo model."""
from __future__ import annotations

import base64
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage

from ..photocraft_bridge import PhotoCraftControl
from ..scanner import normalized_path_key

LOG = logging.getLogger(__name__)


class EditorFilmstrip(QObject):
    _replied = Signal(object, int, object, object)

    def __init__(self, host, photos, thumbs, preview, exchange=None):
        super().__init__(host)
        self.host, self.photos, self.thumbs, self.preview = host, photos, thumbs, preview
        self.exchange = exchange or self._exchange
        self.generation, self.ack = 1, 0
        self.first, self.last = 0, 16
        self.sent = {}
        self.wanted = set()
        self.future = None
        self.closed = False
        self.proc = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qml-editor-filmstrip")
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self._replied.connect(self.apply)
        photos.modelReset.connect(self.reset)
        host.opened.connect(self.opened)
        host.stateChanged.connect(self.state_changed)

    def reset(self):
        self.generation += 1
        self.first, self.last, self.ack = 0, 16, 0
        self.sent.clear()

    def opened(self, path):
        if self.closed:
            return
        row = self.photos.row_for_path(path)
        if row >= 0:
            self.preview.go(row)
        proc = self.host._proc
        if proc is not self.proc:
            self.proc = proc
            self.reset()
        if proc is not None and getattr(proc.control, "filmstrip_supported", False):
            self.timer.start()
            self.tick()

    def state_changed(self):
        if self.host.state == "idle":
            self.timer.stop()
            self.release_thumbs(set())
            self.sent.clear()

    def release_thumbs(self, desired):
        for path in self.wanted - desired:
            self.thumbs.unwant(path)
        self.wanted.intersection_update(desired)

    def packet(self):
        total = self.photos.rowCount()
        current = self.photos.row_for_path(self.host.path)
        first = min(self.first, max(0, total - 1))
        positions = list(range(first, min(total, self.last, first + 31)))
        if current >= 0 and current not in positions:
            positions.append(current)
        rows = [(index, self.photos.row_at(index)) for index in positions]
        self.release_thumbs({row["path"] for _, row in rows if row})
        visible_ids = {normalized_path_key(row["path"]) for _, row in rows if row}
        self.sent = {key: version for key, version in self.sent.items() if key in visible_ids}
        items, uploaded = [], None
        for index, row in rows:
            if row is None:
                continue
            path = row["path"]
            ident = normalized_path_key(path)
            item = {"index": index, "id": ident, "name": Path(path).name,
                    "rating": int(row.get("rating", 0)), "rejected": row.get("decision") == "rej",
                    "edited": bool(row.get("edited", False))}
            if path not in self.wanted:
                self.thumbs.url(path, 240)
                self.wanted.add(path)
            cached = self.thumbs.cached_file(path, 240)
            if cached:
                # Cache filenames include source revision and size. Decode only
                # the single thumbnail being uploaded, not the entire window.
                version = hashlib.sha256(cached.encode("utf-8")).hexdigest()
                item["version"] = version
                if uploaded is None and self.sent.get(ident) != version:
                    image = QImage(cached).scaled(384, 384, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    if not image.isNull():
                        image = image.convertToFormat(QImage.Format_RGBA8888)
                        rgba = bytes(image.constBits())
                        if len(rgba) != image.width() * image.height() * 4:
                            raise ValueError("Unexpected filmstrip thumbnail row padding")
                        item["thumbnail"] = {"width": image.width(), "height": image.height(),
                                             "rgba": base64.b64encode(rgba).decode("ascii")}
                        uploaded = (ident, version)
            items.append(item)
        return {"generation": self.generation, "total": total, "current": max(0, current),
                "enabled": total > 0 and current >= 0, "items": items, "ack": self.ack}, uploaded

    def tick(self):
        proc = self.host._proc
        if self.closed or proc is None or self.host.state != "editing" or self.photos.loading:
            return
        if self.future is not None and not self.future.done():
            return
        try:
            packet, uploaded = self.packet()
        except Exception:
            LOG.exception("Could not prepare the QML editor filmstrip")
            return
        generation = self.generation
        self.future = self.executor.submit(self.exchange, proc, packet)

        def finished(future):
            try:
                reply = future.result()
            except Exception as error:
                reply = error
            try:
                self._replied.emit(proc, generation, reply, uploaded)
            except RuntimeError:
                pass

        self.future.add_done_callback(finished)

    @staticmethod
    def _exchange(proc, packet):
        control = PhotoCraftControl(proc.port, proc.token, timeout=2.0)
        try:
            return control.call("ui.filmstrip", packet)
        finally:
            control.close()

    def apply(self, proc, generation, reply, uploaded):
        if self.closed or proc is not self.host._proc or generation != self.generation or self.host.state != "editing" or self.photos.loading:
            return
        if isinstance(reply, Exception):
            LOG.debug("Native filmstrip exchange failed; retrying: %s", reply)
            return
        if reply.get("generation") != generation:
            return
        if uploaded:
            self.sent[uploaded[0]] = uploaded[1]
        first, last = reply.get("first", 0), reply.get("last", 16)
        if type(first) is int and type(last) is int:
            self.first = max(0, first)
            self.last = min(self.first + 32, max(self.first + 1, last))
        request = reply.get("request")
        if not isinstance(request, dict) or request.get("generation") != generation:
            return
        serial, index = request.get("serial"), request.get("index")
        if type(serial) is not int or serial <= self.ack or type(index) is not int:
            return
        row = self.photos.row_at(index)
        if row is None or request.get("id") != normalized_path_key(row["path"]):
            return
        kind, value = request.get("kind"), request.get("value")
        if kind in ("select", "navigate"):
            target = index if kind == "select" else value
            if type(target) is not int:
                return
            selected = self.photos.row_at(target)
            if selected is None:
                return
            self.ack = serial
            self.host.openPath(selected["path"])
        elif kind == "rating" and type(value) is int and 0 <= value <= 5:
            self.ack = serial
            self.photos.setRating(index, value)
        elif kind == "reject":
            self.ack = serial
            self.photos.setDecision(index, "rej")

    def stop(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.release_thumbs(set())
        self.photos.modelReset.disconnect(self.reset)
        self.host.opened.disconnect(self.opened)
        self.host.stateChanged.disconnect(self.state_changed)
        self.executor.shutdown(wait=False, cancel_futures=True)
