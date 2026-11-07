"""Persistent PhotoCraft proxy worker and bounded, in-memory QML display frames."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage, QColorSpace
from PySide6.QtQuick import QQuickImageProvider

from .. import photocraft_bridge as bridge
from ..full_render import _without_host_settings

_frames = OrderedDict()
_frame_lock = threading.Lock()


class _Images(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)

    def requestImage(self, key, size, requested):
        with _frame_lock:
            image = _frames.get(key, QImage())
        size.setWidth(image.width())
        size.setHeight(image.height())
        return image


def install_images(engine):
    if not engine.property("rawPreviewImagesInstalled"):
        engine.addImageProvider("rawpreview", _Images())
        engine.setProperty("rawPreviewImagesInstalled", True)


def put_frame(key, image):
    with _frame_lock:
        _frames[key] = image
        while len(_frames) > 8 or (len(_frames) > 1 and sum(frame.sizeInBytes() for frame in _frames.values()) > 256 * 1024 * 1024):
            _frames.popitem(last=False)
    return "image://rawpreview/" + key


def has_frame(url):
    with _frame_lock:
        return url.removeprefix("image://rawpreview/") in _frames


def clear_frames(owner):
    with _frame_lock:
        for key in list(_frames):
            if key.startswith(owner + "/"):
                del _frames[key]


def discard_frame(url):
    with _frame_lock:
        _frames.pop(url.removeprefix("image://rawpreview/"), None)


def executable():
    override = os.environ.get("IMAGE_TRIAGE_RAW_PREVIEW_EXE")
    if override:
        return override if Path(override).is_file() else None
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).with_name("image-triage-raw-preview.exe")
        return str(candidate) if candidate.is_file() else None
    return None


def histogram(header):
    channels = [[sum(channel[i:i+4]) for i in range(0, 256, 4)] for channel in header["histogram"]]
    peak = max(1, *(max(c[1:-1], default=0) for c in channels))
    scope = max(1, *(max(c, default=0) for c in channels))
    result = {"ready": True, "summary": "RAW working preview"}
    for key, counts in zip(("r", "g", "b"), channels):
        result[key] = [min(1, value / peak) for value in counts]
        result["scope_" + key] = [value / scope for value in counts]
    return result


class Connection:
    def __init__(self, path, own=None):
        self.log = tempfile.TemporaryFile()
        self.proc = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if own:
            own(self)
        try:
            if self.call({"op": "hello"})[0].get("protocol") != 2:
                raise RuntimeError("The RAW preview worker needs to be rebuilt.")
        except Exception:
            self.close()
            raise

    def call(self, request):
        self.proc.stdin.write((json.dumps(request, allow_nan=False) + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline(128 * 1024)
        if not line:
            raise RuntimeError("The RAW preview worker stopped.")
        header = json.loads(line)
        if not header.get("ok"):
            raise RuntimeError(header.get("error", "RAW preview failed."))
        size = header.get("bytes")
        if type(size) is not int or not 0 <= size <= 32 * 1024 * 1024:
            raise RuntimeError("Invalid RAW preview frame size.")
        if not size:
            return header, QImage()
        width, height = header.get("width"), header.get("height")
        if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
                or width * height * 4 != size):
            raise RuntimeError("Invalid RAW preview dimensions.")
        data = bytearray()
        while len(data) < size:
            chunk = self.proc.stdout.read(size - len(data))
            if not chunk:
                raise RuntimeError("Incomplete RAW preview frame.")
            data.extend(chunk)
        image = QImage(data, width, height, width * 4, QImage.Format_RGBA8888).copy()
        image.setColorSpace(QColorSpace(QColorSpace.SRgb))
        return header, image

    def interrupt(self):
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                self.interrupt()
                self.proc.wait(timeout=2)
        self.log.close()


class PreviewWorker(QObject):
    completed = Signal(int, object)
    sampled = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._path = path
        self._condition = threading.Condition()
        self._queued = None
        self._connection = None
        self._thread = None
        self._closing = False

    def submit(self, revision, path, original, draft, quality="settled", sample=None):
        with self._condition:
            self._queued = revision, path, deepcopy(original), deepcopy(draft), quality, sample
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="raw-preview-worker")
                self._thread.start()
            self._condition.notify()

    def _run(self):
        source = None
        baseline = None
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closing or self._queued is not None)
                    if self._closing:
                        return
                    revision, path, original, draft, quality, sample = self._queued
                    self._queued = None
                started = time.perf_counter()
                try:
                    signature = bridge.file_signature(path)
                    if not signature or bridge.sidecar_pcraft_path(path).exists():
                        raise RuntimeError("The RAW changed or has a PhotoCraft project. Reopen the editor.")
                    state, recipe, reason = bridge.recipe_state(path)
                    settings = dict(recipe["cameraRaw"]) if recipe else {}
                    if state == bridge.RECIPE_INVALID or settings != original:
                        raise RuntimeError(reason or "The saved recipe changed. Reopen Edit RAW.")
                    if self._connection is None:
                        def own(connection):
                            self._connection = connection
                            if self._closing:
                                connection.interrupt()
                        self._connection = Connection(self._path, own)
                        source = None
                    request = {"revision": revision, "quality": quality, "backend": os.environ.get("IMAGE_TRIAGE_RAW_PREVIEW_BACKEND", "cpu")}
                    source_key = (os.path.normcase(os.path.abspath(path)), signature, json.dumps(original, sort_keys=True))
                    if source != source_key:
                        baseline = self._connection.call({**request, "quality": "settled", "op": "open", "path": path, "edge": 1800,
                            "recipe": _without_host_settings({"version": 1, "cameraRaw": original})})
                        source = source_key
                    result = (self._connection.call({**request, "op": "render", "recipe": {"version": 1, "cameraRaw": {}}})
                        if sample is not None else baseline if draft == original else self._connection.call({**request, "op": "render",
                        "recipe": _without_host_settings({"version": 1, "cameraRaw": draft})}))
                    if bridge.file_signature(path) != signature:
                        raise RuntimeError("The RAW changed during preview. Reopen the editor.")
                    if sample is not None:
                        self.sampled.emit(revision, (result[1], sample))
                    else:
                        self.completed.emit(revision, (result, baseline, (time.perf_counter()-started)*1000, path))
                except Exception as error:
                    if self._connection:
                        self._connection.close()
                        self._connection = None
                    source = baseline = None
                    if not self._closing:
                        self.failed.emit(revision, str(error))
        finally:
            if self._connection:
                self._connection.close()
                self._connection = None

    def close(self):
        with self._condition:
            self._closing = True
            self._queued = None
            self._condition.notify()
        if self._connection:
            self._connection.interrupt()
        if self._thread:
            self._thread.join(timeout=4)
