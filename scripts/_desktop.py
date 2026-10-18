"""Keep live checks behind other apps, optionally on a secondary physical monitor.

A new window opens on whichever virtual desktop is active at that moment, so a check started while its owner is working on
another desktop lands on top of that work. With ``IMAGE_TRIAGE_CHECK_DESKTOP_OF`` set to a process name (``claude.exe``),
the desktop holding that program's window is the checks' home: ``wait_for_home_desktop`` holds a check back until that
desktop is the active one, and ``pin`` moves a window of this process there.
``IMAGE_TRIAGE_CHECK_MONITOR=secondary`` selects a non-primary physical screen and fails
before showing a window if none exists. A screen name can also be specified. Protected
windows cannot accept focus; this helper never restores focus by activating another app.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import sys
import time

MARKER_ENV = "IMAGE_TRIAGE_CHECK_DESKTOP_OF"
MONITOR_ENV = "IMAGE_TRIAGE_CHECK_MONITOR"


def _check_screen(app):
    """Resolve the opt-in physical monitor before creating a native window; fail closed."""
    requested = os.environ.get(MONITOR_ENV, "").strip()
    if not requested:
        return None
    screens = app.screens()
    if requested == "secondary":
        choices = [screen for screen in screens if screen != app.primaryScreen()]
    else:
        choices = [screen for screen in screens if screen.name() == requested]
    if not choices:
        raise RuntimeError(f"Check monitor {requested!r} is unavailable; no test window was opened")
    return choices[0]


def _prevent_activation(hwnd: int) -> None:
    """Apply WS_EX_NOACTIVATE before first show, including after HWND recreation."""
    user32 = ctypes.windll.user32
    get_style = user32.GetWindowLongW
    set_style = user32.SetWindowLongW
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = wintypes.LONG
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    set_style.restype = wintypes.LONG
    style = get_style(hwnd, -20)  # GWL_EXSTYLE
    set_style(hwnd, -20, style | 0x08000000)  # WS_EX_NOACTIVATE
    if not get_style(hwnd, -20) & 0x08000000:
        raise OSError("Cannot prevent test window activation")


class _Guid(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    def is_null(self) -> bool:
        return not any(bytes(self))


class _Desktops:
    """IVirtualDesktopManager (documented shell interface): which desktop a window is on, and moving our own windows."""

    def __init__(self) -> None:
        ole32 = ctypes.windll.ole32
        clsid, iid = _Guid(), _Guid()
        ole32.CLSIDFromString(ctypes.c_wchar_p("{AA509086-5CA9-4C25-8F95-589D3C07B48A}"), ctypes.byref(clsid))
        ole32.CLSIDFromString(ctypes.c_wchar_p("{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}"), ctypes.byref(iid))
        ole32.CoInitialize(None)
        self._manager = ctypes.c_void_p()
        if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1 | 4, ctypes.byref(iid), ctypes.byref(self._manager)) != 0:
            raise OSError("the virtual desktop manager is unavailable")
        vtable = ctypes.cast(ctypes.cast(self._manager, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        self._is_current = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.POINTER(wintypes.BOOL))(vtable[3])
        self._desktop_of = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.POINTER(_Guid))(vtable[4])
        self._move = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.POINTER(_Guid))(vtable[5])

    def is_current(self, hwnd: int) -> bool:
        current = wintypes.BOOL()
        return self._is_current(self._manager, hwnd, ctypes.byref(current)) == 0 and bool(current.value)

    def desktop_of(self, hwnd: int) -> _Guid | None:
        desktop = _Guid()
        if self._desktop_of(self._manager, hwnd, ctypes.byref(desktop)) != 0 or desktop.is_null():
            return None
        return desktop

    def move(self, hwnd: int, desktop: _Guid) -> bool:
        return self._move(self._manager, hwnd, ctypes.byref(desktop)) == 0


def _marker_window(process_name: str) -> int | None:
    """A visible, titled top-level window of ``process_name`` that belongs to a virtual desktop."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    desktops = _state()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return True
        name = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size))
        kernel32.CloseHandle(handle)
        if name.value.rsplit("\\", 1)[-1].lower() == process_name.lower() and desktops.desktop_of(hwnd) is not None:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


_desktops: _Desktops | None = None


def _state() -> _Desktops:
    global _desktops
    if _desktops is None:
        _desktops = _Desktops()
    return _desktops


def _home() -> tuple[int, _Guid] | None:
    marker = os.environ.get(MARKER_ENV, "").strip()
    if not marker or sys.platform != "win32":
        return None
    try:
        hwnd = _marker_window(marker)
        desktop = _state().desktop_of(hwnd) if hwnd else None
    except OSError:
        return None
    return (hwnd, desktop) if hwnd and desktop is not None else None


def home_is_active() -> bool:
    """Whether the home desktop is the one on screen (true when no home is configured)."""
    home = _home()
    return home is None or _state().is_current(home[0])


def wait_for_home_desktop(timeout: float = 3600.0) -> None:
    """Returns once the home desktop is the active one, so the windows about to open land there."""
    if home_is_active():
        return
    print(f"Waiting for the desktop with {os.environ[MARKER_ENV]} to be the active one before opening any window...", flush=True)
    deadline = time.monotonic() + timeout
    while not home_is_active():
        if time.monotonic() > deadline:
            raise SystemExit("The home desktop did not become active; nothing was opened.")
        time.sleep(1.0)


def pin(widget) -> None:
    """Moves a top-level Qt window of this process to the home desktop. Call it before the window is shown."""
    home = _home()
    if home is None:
        return
    try:
        _state().move(int(widget.winId()), home[1])
    except OSError:
        pass


def _send_to_back(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_BOTTOM; no size, no move, no activate


def place(widget) -> None:
    """Place a hidden test window before showing it. No-op without a desktop or monitor request."""
    from PySide6.QtWidgets import QApplication

    screen = _check_screen(QApplication.instance())
    if _home() is None and screen is None:
        return
    from PySide6.QtCore import QEvent, QObject, Qt, QTimer

    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    widget.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    if screen is not None:
        widget.setScreen(screen)
        available = screen.availableGeometry()
        widget.resize(min(widget.width(), available.width()), min(widget.height(), available.height()))
        widget.move(available.center() - widget.rect().center())
    pin(widget)
    _prevent_activation(int(widget.winId()))

    class _Behind(QObject):
        def __init__(self, parent):
            super().__init__(parent)
            self.pending = False

        def settle(self):
            self.pending = False
            try:
                hwnd = int(widget.winId())
            except RuntimeError:  # the window is gone
                return
            pin(widget)  # a window re-created by a state change (full screen) starts on the active desktop again
            _prevent_activation(hwnd)
            _send_to_back(hwnd)

        def eventFilter(self, watched, event):
            # Nothing here may ask the window for its handle: that creates it, which sends the events this filter listens for.
            if event.type() in (QEvent.Type.Show, QEvent.Type.WindowActivate, QEvent.Type.WindowStateChange, QEvent.Type.WinIdChange):
                if not self.pending:
                    self.pending = True
                    QTimer.singleShot(0, self.settle)
            return False

    widget.installEventFilter(_Behind(widget))
