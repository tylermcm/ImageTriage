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
_thumbnails = OrderedDict()
_frame_lock = threading.Lock()


class _Images(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)

    def requestImage(self, key, size, requested):
        key, _, clip = key.partition("?crop=")
        with _frame_lock:
            image = (_thumbnails if key.startswith("thumb/") else _frames).get(key, QImage())
        if clip and not image.isNull():
            try:
                x, y, w, h = [int(v) for v in clip.split(",")]
                if 0 <= x < image.width() and 0 <= y < image.height() and 0 < w <= image.width()-x and 0 < h <= image.height()-y:
                    image = image.copy(x, y, w, h)
            except ValueError:
                pass
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


def frame_image(url):
    """Uncropped display pixels for host AI tools; Qt shares the immutable buffer."""
    with _frame_lock:
        return QImage(_frames.get(url.removeprefix("image://rawpreview/"), QImage()))


def clear_frames(owner):
    with _frame_lock:
        for key in list(_frames):
            if key.startswith(owner + "/"):
                del _frames[key]
        for key in list(_thumbnails):
            if key.startswith("thumb/"+owner+"/"):del _thumbnails[key]


def put_thumbnail(key,image):
    key="thumb/"+key
    with _frame_lock:
        _thumbnails[key]=image
        while len(_thumbnails)>32:_thumbnails.popitem(last=False)
    return "image://rawpreview/"+key


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


# How long one request to the preview worker may take before the worker is taken to have hung and is stopped. Opening a large
# RAW on a slow disk takes seconds; nothing it does takes minutes.
CALL_TIMEOUT_S = 120.0
ERROR_TAIL_BYTES = 2000


class Connection:
    timeout = None          # seconds for one request; None uses CALL_TIMEOUT_S

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
        """One request and its reply. A worker that does not answer in time is stopped, so the thread waiting here (and the
        editor's "working" state with it) is never left waiting for good."""
        limit = CALL_TIMEOUT_S if self.timeout is None else self.timeout
        hung = threading.Event()

        def stop():
            hung.set()
            self.interrupt()

        watchdog = threading.Timer(limit, stop)
        watchdog.daemon = True
        watchdog.start()
        try:
            return self._exchange(request)
        except Exception as error:
            if hung.is_set():
                raise RuntimeError(f"The RAW preview worker did not answer within {limit:g} s and was stopped." + self._error_tail()) from error
            raise
        finally:
            watchdog.cancel()

    def _error_tail(self):
        """The end of what the worker wrote to its error output, for the message of a worker that stopped. Read only once the
        worker has exited: until then it is still writing through the same file position."""
        proc, log = self.proc, getattr(self, "log", None)
        if log is None:
            return ""
        try:
            proc.wait(timeout=1)
            size = log.seek(0, os.SEEK_END)
            log.seek(max(0, size - ERROR_TAIL_BYTES))
            text = log.read().decode("utf-8", "replace").strip()
        except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
            return ""
        return f" Its last output: {text}" if text else ""

    def _exchange(self, request):
        try:
            self.proc.stdin.write((json.dumps(request, allow_nan=False) + "\n").encode())
            self.proc.stdin.flush()
        except OSError as error:
            raise RuntimeError("The RAW preview worker stopped." + self._error_tail()) from error
        line = self.proc.stdout.readline(128 * 1024)
        if not line:
            raise RuntimeError("The RAW preview worker stopped." + self._error_tail())
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
                raise RuntimeError("Incomplete RAW preview frame." + self._error_tail())
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
    maskSampled = Signal(int, object)
    coverageReady = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._path = path
        self._condition = threading.Condition()
        self._queued = None
        self._connection = None
        self._thread = None
        self._closing = False

    def submit(self, revision, path, original, draft, quality="settled", sample=None, coverage=None):
        with self._condition:
            self._queued = revision, path, deepcopy(original), deepcopy(draft), quality, sample, deepcopy(coverage)
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
                    revision, path, original, draft, quality, sample, coverage = self._queued
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
                    if isinstance(sample, dict):
                        settings = {k:v for k,v in draft.items() if k != "masks"}
                        result = self._connection.call({**request,"op":"render","recipe":_without_host_settings({"version":1,"cameraRaw":settings}),"sample":sample["area"]})
                        if "sampleRGB" not in result[0]:
                            raise RuntimeError("Range sampling needs the updated RAW preview helper.")
                    else:
                        result = (self._connection.call({**request, "op": "render", "recipe": {"version": 1, "cameraRaw": {}}})
                        if sample is not None else baseline if draft == original else self._connection.call({**request, "op": "render",
                        "recipe": _without_host_settings({"version": 1, "cameraRaw": draft})}))
                    if bridge.file_signature(path) != signature:
                        raise RuntimeError("The RAW changed during preview. Reopen the editor.")
                    if isinstance(sample, dict):
                        self.maskSampled.emit(revision,(result[0],sample))
                    elif sample is not None:
                        self.sampled.emit(revision, (result[1], sample))
                    else:
                        self.completed.emit(revision, (result, baseline, (time.perf_counter()-started)*1000, path))
                        if coverage:
                            settings={k:v for k,v in draft.items() if k!="masks"}
                            header,image=self._connection.call({**request,"op":"render","recipe":_without_host_settings({"version":1,"cameraRaw":settings}),"coverage":coverage})
                            if not header.get("maskCoverage"):raise RuntimeError("Mask overlays need the updated RAW preview helper.")
                            self.coverageReady.emit(revision,(image,coverage))
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
