"""The quick viewer's window model (``QuickView.qml``) and its image provider. See the package's docstring."""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Property, QObject, QSize, QTimer, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from . import FULL_APP_WAIT_S, decode, full_app_command, mark

CACHE_SIZE = 5                     # decoded pictures kept: this one and two either side


class _Provider(QQuickImageProvider):
    """``image://quick/<n>``: the picture token ``n`` stands for, waited for on the QML image loader's thread."""

    def __init__(self, viewer: "QuickViewer") -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._viewer = viewer

    def requestImage(self, ident: str, size: QSize, requested: QSize) -> QImage:  # noqa: N802 (Qt's name)
        picture = self._viewer.picture_for(ident)
        image = picture.image if picture is not None else QImage()
        if size is not None and not image.isNull():
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image


class QuickViewer(QObject):
    changed = Signal()
    _ready = Signal(str)                       # a token whose picture is decoded (from a worker thread)
    _list_arrived = Signal(list)               # the folder's photos (from a worker thread)

    def __init__(self, photo: str, app=None, longest: int | None = None, launcher=None) -> None:
        super().__init__()
        self._app = app
        self._photos: list[str] = [os.path.abspath(photo)]
        self._index = 0
        self._longest = longest or _screen_longest()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quick-view")
        self._lock = threading.Lock()
        self._futures: "OrderedDict[str, Future]" = OrderedDict()      # "<path>|<longest>" -> its decode
        self._token = ""                       # "<path>|<longest>": the picture asked for
        self._ids: dict[str, str] = {}         # the numbers in image:// sources -> tokens (a path is no part of a URL)
        self._shown: decode.Picture | None = None
        self._shown_token = ""                 # the token `_shown` is the picture of
        self._full = False                     # the shown picture is the full-size one (asked for when zooming past the screen size)
        self._listed = False
        self._library = ""                     # "", "starting" (the library is being started) or "failed"
        self._launcher = launcher              # tests: called instead of starting a process
        self._process = None
        self._window = None
        self._first_shown = False
        self.provider = _Provider(self)
        self._ready.connect(self._arrived)
        self._list_arrived.connect(self._take_list)

    # -- decoding ------------------------------------------------------------------------------------------------------------------------
    def _key(self, path: str, longest: int) -> str:
        return f"{path}|{longest}"

    def _decode(self, path: str, longest: int) -> Future:
        key = self._key(path, longest)
        with self._lock:
            future = self._futures.get(key)
            if future is None:
                future = self._pool.submit(decode.load, path, longest)
                self._futures[key] = future
            self._futures.move_to_end(key)
            while len(self._futures) > CACHE_SIZE + 1:
                self._futures.popitem(last=False)
        return future

    def picture_for(self, ident: str) -> decode.Picture | None:
        """The picture for an ``image://quick/<n>`` source, waiting for its decode (on QML's loader thread)."""
        key = self._ids.get(ident.split("#", 1)[0], "")
        if not key:
            return None
        path, _, longest = key.rpartition("|")
        try:
            return self._decode(path, int(longest)).result(timeout=60)
        except Exception:
            return None

    def start(self) -> None:
        self._show(0)

    def _show(self, index: int, full: bool = False) -> None:
        self._index = index
        self._full = full
        path = self._photos[index]
        longest = 0 if full else self._longest
        future = self._decode(path, longest)
        token = self._key(path, longest)
        self._token = token
        if token not in self._ids.values():
            self._ids[str(len(self._ids) + 1)] = token
        future.add_done_callback(lambda _f, t=token: self._ready.emit(t))
        self.changed.emit()

    def _arrived(self, token: str) -> None:
        if token != self._token:
            return
        path, _, longest = token.rpartition("|")
        picture = self._decode(path, int(longest)).result()
        self._shown, self._shown_token = picture, token
        self.changed.emit()
        if not self._first_shown:
            self._first_shown = True
            mark("photo decoded")
            QTimer.singleShot(0, self._after_first)
        else:
            self._prefetch()

    def _after_first(self) -> None:
        """Once the first photo is up: the folder's other photos, the neighbours decoded ahead, then the library's files warmed."""
        def listed(future: Future) -> None:
            if not future.cancelled() and future.exception() is None:
                self._list_arrived.emit(future.result())

        self._pool.submit(decode.folder_photos, self._photos[0]).add_done_callback(listed)
        QTimer.singleShot(2500, lambda: threading.Thread(target=warm_library_files, name="quick-view-warm", daemon=True).start())

    def _take_list(self, photos: list) -> None:
        current = self._photos[self._index]
        key = os.path.normcase(current)
        self._photos = list(photos) or [current]
        self._index = next((i for i, p in enumerate(self._photos) if os.path.normcase(p) == key), 0)
        self._listed = True
        self.changed.emit()
        self._prefetch()

    def _prefetch(self) -> None:
        for step in (1, -1, 2):
            other = self._index + step
            if 0 <= other < len(self._photos):
                self._decode(self._photos[other], self._longest)

    # -- what QML shows ------------------------------------------------------------------------------------------------------------------
    def _picture(self) -> decode.Picture | None:
        """The picture on show: the one asked for, once it has arrived (until then, nothing: the last photo's details never show)."""
        return self._shown if self._shown is not None and self._shown_token == self._token else None

    def _source(self) -> str:
        picture = self._picture()
        if picture is None or picture.error:
            return ""
        return "image://quick/" + next(n for n, t in self._ids.items() if t == self._token)

    source = Property(str, _source, notify=changed)
    path = Property(str, lambda self: self._photos[self._index], notify=changed)
    name = Property(str, lambda self: Path(self._photos[self._index]).name, notify=changed)
    folder = Property(str, lambda self: Path(self._photos[self._index]).parent.name, notify=changed)
    index = Property(int, lambda self: self._index, notify=changed)
    count = Property(int, lambda self: len(self._photos), notify=changed)
    listed = Property(bool, lambda self: self._listed, notify=changed)
    error = Property(str, lambda self: self._picture().error if self._picture() is not None else "", notify=changed)
    loading = Property(bool, lambda self: self._picture() is None, notify=changed)
    photoWidth = Property(int, lambda self: self._picture().width if self._picture() is not None else 0, notify=changed)
    photoHeight = Property(int, lambda self: self._picture().height if self._picture() is not None else 0, notify=changed)
    kind = Property(str, lambda self: self._picture().kind if self._picture() is not None else "", notify=changed)
    full = Property(bool, lambda self: self._full, notify=changed)
    library = Property(str, lambda self: self._library, notify=changed)

    @Slot(int)
    def go(self, step: int) -> None:
        """``step`` photos on (negative: back), stopping at the ends."""
        target = max(0, min(len(self._photos) - 1, self._index + step))
        if target != self._index:
            self._show(target)

    @Slot(int)
    def goTo(self, index: int) -> None:
        if 0 <= index < len(self._photos) and index != self._index:
            self._show(index)

    @Slot()
    def wantFull(self) -> None:
        """Zoomed past the screen size: the photo at its full size (a RAW's embedded preview is as large as it gets)."""
        picture = self._picture()
        if self._full or picture is None or picture.image.isNull():
            return
        if max(picture.image.width(), picture.image.height()) >= max(picture.width, picture.height):
            return
        self._show(self._index, full=True)

    @Slot()
    def firstFrame(self) -> None:
        mark("first frame")

    @Slot()
    def photoFrame(self) -> None:
        if not getattr(self, "_photo_framed", False):
            self._photo_framed = True
            mark("photo on screen")
            grab = os.environ.get("IMAGE_TRIAGE_QUICK_VIEW_GRAB", "")
            if grab and self._window is not None:                  # headless checks: the window as drawn, then quit
                def save() -> None:
                    self._window.grabWindow().save(grab)
                    if self._app is not None:
                        self._app.quit()

                QTimer.singleShot(int(os.environ.get("IMAGE_TRIAGE_QUICK_VIEW_GRAB_WAIT", "600")), save)

    # -- the library -----------------------------------------------------------------------------------------------------------------------
    @Slot()
    def openLibrary(self) -> None:
        """The full app on this photo. A running one takes it at once; otherwise it is started and this window closes when it is up."""
        if self._library == "starting":
            return
        photo = self._photos[self._index]
        from ..qml_shell.single_instance import send_to_running

        if send_to_running({"folder": os.path.dirname(photo), "select": photo}, timeout_ms=1500):
            self._close()
            return
        self._library = "starting"
        self.changed.emit()
        try:
            if self._launcher is not None:
                self._launcher(full_app_command(photo))
            else:
                import subprocess

                flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
                self._process = subprocess.Popen(full_app_command(photo), close_fds=True, creationflags=flags,
                                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self._library = "failed"
            self.changed.emit()
            return
        self._waited = time.monotonic()
        QTimer.singleShot(250, self._wait_for_library)

    def _wait_for_library(self) -> None:
        """Closes once the library listens (it is up, its own launch window showing); gives up, and stays, if it never does."""
        from PySide6.QtNetwork import QLocalSocket

        from ..qml_shell.single_instance import server_name

        socket = QLocalSocket()
        socket.connectToServer(server_name())
        up = socket.waitForConnected(100)
        socket.abort()
        if up:
            QTimer.singleShot(700, self._close)            # its launch window is up by then: no moment with no window
            return
        ended = self._process is not None and self._process.poll() is not None
        if ended or time.monotonic() - self._waited > FULL_APP_WAIT_S:
            self._library = "failed"
            self.changed.emit()
            return
        QTimer.singleShot(250, self._wait_for_library)

    def _close(self) -> None:
        if self._window is not None:
            self._window.close()
        if self._app is not None:
            self._app.quit()

    def attach(self, window) -> None:
        self._window = window

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _screen_longest() -> int:
    """The long side of the largest screen, in device pixels: a picture bigger than that is not seen until zoomed in."""
    try:
        from PySide6.QtGui import QGuiApplication

        sides = [max(s.size().width(), s.size().height()) * s.devicePixelRatio() for s in QGuiApplication.screens()]
        return int(min(7680, max(1600, max(sides) if sides else 2560)))
    except Exception:
        return 2560


def warm_library_files(budget: int = 160 * 1024 * 1024) -> int:
    """Read the library's own files once, slowly, so that Library (or the next launch) finds them in the disk cache. Returns the bytes
    read. The app's modules and QML first, then the Qt Quick modules only the library uses."""
    roots = [Path(__file__).resolve().parents[1]]
    try:
        import PySide6

        qml = Path(PySide6.__file__).resolve().parent / "qml"
        roots += [qml / "QtQuick" / name for name in ("Controls", "Dialogs", "Effects", "Shapes", "Layouts")]
    except Exception:
        pass
    done = 0
    for root in roots:
        for folder, _dirs, files in os.walk(root):
            for name in files:
                if done >= budget:
                    return done
                try:
                    with open(os.path.join(folder, name), "rb") as handle:
                        while chunk := handle.read(1 << 20):
                            done += len(chunk)
                            time.sleep(0.001)        # a trickle, so the photo on screen and its neighbours are never kept waiting
                except OSError:
                    continue
    return done
