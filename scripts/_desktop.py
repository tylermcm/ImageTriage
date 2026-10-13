"""Keep a live check's windows on one Windows virtual desktop.

A new window opens on whichever virtual desktop is active at that moment, so a check started while its owner is working on
another desktop lands on top of that work. With ``IMAGE_TRIAGE_CHECK_DESKTOP_OF`` set to a process name (``claude.exe``),
the desktop holding that program's window is the checks' home: ``wait_for_home_desktop`` holds a check back until that
desktop is the active one, and ``pin`` moves a window of this process there. Without the variable both do nothing.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import sys
import time

MARKER_ENV = "IMAGE_TRIAGE_CHECK_DESKTOP_OF"


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
