"""PocketDrop's engine as a model for a host that draws its own interface (the QML shell).

PocketDrop's shared C++ (native/pocketdrop/src) keeps all of its state and behaviour; this host gives it the operating-system
services it asks for (clipboard, settings, opening links) and exposes what it would have painted as JSON (state()) and what a
click would have done as named commands (command()). Nothing is drawn here: the pd_gfx table is empty because pd_paint is never
called.

Everything the engine calls back runs on the thread that owns this object, except wake(), which the engine's workers call to
have pd_run_posted() run here.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage

from ..app_identity import user_settings
from . import _bridge as pd

SETTINGS_GROUP = "pocketdrop"
SLOW_TICK_MS = 500
FAST_TICK_MS = 33
# Only pd_paint, pd_mouse_* and the menu use the window size; these are PocketDrop's design width and a typical height.
LAYOUT_SIZE = (385.0, 750.0)


class PocketDropHost(QObject):
    changed = Signal()                       # the engine's state moved on: read state() again
    alertRequested = Signal(str, str)        # title, message (About, and errors)

    _woken = Signal()

    def __init__(self, lib: ctypes.CDLL, settings=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lib = lib
        self._host: int | None = None
        self._settings = settings if settings is not None else user_settings()
        self._risk_ok = False
        self._risk_dismiss = False
        self._topmost_window = 0
        self._slow = QTimer(self)
        self._slow.setInterval(SLOW_TICK_MS)
        self._slow.timeout.connect(self._tick)
        self._fast = QTimer(self)
        self._fast.setInterval(FAST_TICK_MS)
        self._fast.timeout.connect(self._tick)
        self._woken.connect(self._run_posted, Qt.ConnectionType.QueuedConnection)

        self._gfx = pd.Gfx()   # all callbacks null: the engine never paints here
        self._shell = pd.Shell(
            None,
            pd.VOID(lambda _ctx: self.changed.emit()),
            pd.SET_FLAG(self._s_set_animating),
            pd.VOID(lambda _ctx: self._woken.emit()),
            pd.CLIENT_SIZE(self._s_client_size),
            pd.WITH_TEXT(lambda _ctx, value: QGuiApplication.clipboard().setText(pd.text(value))),
            pd.COPY_IMAGE(self._s_copy_image),
            pd.INTO_SINK(self._s_read_clipboard),
            pd.BROWSE(lambda _ctx, _folders, _sink: None),           # the QML shell's own file dialogs add paths instead
            pd.TEXT_INTO_SINK(lambda _ctx, _title, _sink: None),
            pd.WITH_TEXT(lambda _ctx, url: QDesktopServices.openUrl(QUrl(pd.text(url)))),
            pd.VOID(self._s_open_bluetooth_setup),
            pd.CONFIRM(self._s_confirm_anywhere_risk),
            pd.WITH_TEXT(lambda _ctx, path: reveal_in_file_manager(pd.text(path))),
            pd.VOID(lambda _ctx: QGuiApplication.alert(QGuiApplication.focusWindow())),
            pd.POPUP_MENU(lambda _ctx, _items, _x, _y: 0),
            pd.ALERT(lambda _ctx, title, message: self.alertRequested.emit(pd.text(title), pd.text(message))),
            pd.LOAD_SETTING(self._s_load_setting),
            pd.SAVE_SETTING(self._s_save_setting),
            pd.TEXT_INTO_SINK(self._s_load_string),
            pd.SAVE_STRING(self._s_save_string),
            pd.SET_FLAG(self._s_set_topmost),
            pd.TEXT_INTO_SINK(self._s_clean_path),
            pd.NATIVE_WINDOW(lambda _ctx: self._topmost_window),
        )

    # -- lifecycle -------------------------------------------------------------------------------------------------------
    @property
    def started(self) -> bool:
        return self._host is not None

    def start(self) -> bool:
        if self._host is not None:
            return True
        host = self._lib.pd_create(ctypes.byref(self._shell), ctypes.byref(self._gfx))
        if not host:
            return False
        self._host = host
        self._lib.pd_start(host)
        self._slow.start()
        self.changed.emit()
        return True

    def shutdown(self) -> None:
        host, self._host = self._host, None
        self._slow.stop()
        self._fast.stop()
        if host:
            self._lib.pd_destroy(host)

    def set_window(self, handle: int) -> None:
        """The native window handle (HWND) the engine's share sheet and Always on top use."""
        self._topmost_window = int(handle)

    # -- model -----------------------------------------------------------------------------------------------------------
    def state(self) -> dict:
        if self._host is None:
            return {}
        raw = self._lib.pd_state_json(self._host)
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            return {}

    def command(self, name: str, arg: str = "", *, risk_confirmed: bool = False, dismiss_risk: bool = False) -> None:
        """Do what a click on `name` does (see pd_command in capi/pocketdrop_capi.h).

        The engine asks its host to confirm the Anywhere warning from inside the mode change; the shell has already asked the
        person in its own dialog, so the answer is handed over here."""
        if self._host is None:
            return
        self._risk_ok, self._risk_dismiss = risk_confirmed, dismiss_risk
        try:
            self._lib.pd_command(self._host, pd.encode(name), pd.encode(arg))
        finally:
            self._risk_ok = self._risk_dismiss = False

    def add_paths(self, paths: list[str]) -> None:
        paths = [path for path in paths if path]
        if paths and self.start():
            array = (ctypes.c_char_p * len(paths))(*(pd.encode(path) for path in paths))
            self._lib.pd_add_paths(self._host, array, len(paths))

    def remove_paths(self, paths: list[str]) -> None:
        paths = [path for path in paths if path]
        if paths and self._host is not None:
            array = (ctypes.c_char_p * len(paths))(*(pd.encode(path) for path in paths))
            self._lib.pd_remove_paths(self._host, array, len(paths))

    def add_text(self, text: str) -> None:
        if text and self.start():
            self._lib.pd_add_text(self._host, pd.encode(text))

    # -- plumbing --------------------------------------------------------------------------------------------------------
    def _tick(self) -> None:
        if self._host:
            self._lib.pd_tick(self._host)
            self.changed.emit()

    def _run_posted(self) -> None:
        if self._host:
            self._lib.pd_run_posted(self._host)

    # -- pd_shell --------------------------------------------------------------------------------------------------------
    def _s_set_animating(self, _ctx, on) -> None:
        if on:
            self._fast.start()
        else:
            self._fast.stop()

    def _s_client_size(self, _ctx, width, height) -> None:
        width[0], height[0] = LAYOUT_SIZE

    def _s_copy_image(self, _ctx, rgba, width, height) -> int:
        data = ctypes.string_at(rgba, width * height * 4)
        QGuiApplication.clipboard().setImage(QImage(data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy())
        return 1

    def _s_read_clipboard(self, _ctx, sink) -> None:
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None:
            return
        paths = local_paths(mime)
        if paths:
            for path in paths:
                self._lib.pd_sink_add(sink, pd.encode(path))
            return
        if mime.hasImage():
            image = QGuiApplication.clipboard().image()
            if not image.isNull():
                target = pasted_images_dir() / datetime.now().strftime("Pasted image %Y-%m-%d %H.%M.%S.png")
                if image.save(str(target), "PNG"):
                    self._lib.pd_sink_add(sink, pd.encode(str(target)))
                    return
        if mime.hasText():
            self._lib.pd_sink_set(sink, pd.encode(mime.text()))

    def _s_open_bluetooth_setup(self, _ctx) -> None:
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", "shell:::{A8A91A66-3A7D-4424-8D24-04E180695C7A}"])

    def _s_confirm_anywhere_risk(self, _ctx, dont_show) -> int:
        dont_show[0] = 1 if self._risk_dismiss else 0
        return 1 if self._risk_ok else 0

    def _s_load_setting(self, _ctx, key, fallback) -> int:
        value = self._settings.value(f"{SETTINGS_GROUP}/{pd.text(key)}", fallback)
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(fallback)

    def _s_save_setting(self, _ctx, key, value) -> None:
        self._settings.setValue(f"{SETTINGS_GROUP}/{pd.text(key)}", int(value))

    def _s_load_string(self, _ctx, key, sink) -> None:
        name = f"{SETTINGS_GROUP}/{pd.text(key)}"
        if self._settings.contains(name):
            self._lib.pd_sink_set(sink, pd.encode(str(self._settings.value(name, ""))))

    def _s_save_string(self, _ctx, key, value) -> None:
        self._settings.setValue(f"{SETTINGS_GROUP}/{pd.text(key)}", pd.text(value))

    def _s_set_topmost(self, _ctx, on) -> None:
        # Win32 rather than a Qt window flag: changing flags would recreate the frameless window.
        if sys.platform != "win32" or not self._topmost_window:
            return
        topmost, not_topmost = -1, -2
        flags = 0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
        ctypes.windll.user32.SetWindowPos(self._topmost_window, topmost if on else not_topmost, 0, 0, 0, 0, flags)

    def _s_clean_path(self, _ctx, path, sink) -> None:
        value = pd.text(path).strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        if not value:
            return
        value = os.path.abspath(value)
        root = os.path.splitdrive(value)[0] + os.sep
        while len(value) > len(root) and value[-1] in "\\/":
            value = value[:-1]
        if os.path.exists(value):
            self._lib.pd_sink_set(sink, pd.encode(value))


def local_paths(mime) -> list[str]:
    if mime is None or not mime.hasUrls():
        return []
    return [os.path.normpath(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]


def pasted_images_dir() -> Path:
    from ..scan_cache import app_data_root

    folder = app_data_root() / "PocketDrop" / "Pasted"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def reveal_in_file_manager(path: str) -> None:
    if not path:
        return
    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path) or path))
