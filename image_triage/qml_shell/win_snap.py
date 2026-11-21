"""Windows' snapping for the main window, which draws its own title bar.

A window without a frame (``Qt.FramelessWindowHint``) is moved by Windows (``startSystemMove``), but Windows 11 shows its snap
preview, the outline of where a window dragged to an edge or a corner will go, and the snap layouts only for a window with the
resizable frame's styles. So the window gets those styles back (``WS_THICKFRAME``, ``WS_CAPTION``, the minimise and maximise boxes),
and answers ``WM_NCCALCSIZE`` by giving the whole window to its contents: nothing of the system's title bar or border is drawn.
Maximised, Windows places a framed window so that its border hangs off the screen; the contents are kept inside the screen then.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
GWL_STYLE = -16
WM_NCCALCSIZE = 0x0083
SWP_FRAMECHANGED, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0020, 0x0002, 0x0001, 0x0004, 0x0010
SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92
SNAP_STYLES = WS_THICKFRAME | WS_CAPTION | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class _Filter(QAbstractNativeEventFilter):
    """Gives the whole window to its contents (see the module docs), for the windows it was told about."""

    def __init__(self) -> None:
        super().__init__()
        self.windows: set[int] = set()

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) != b"windows_generic_MSG":
                return False, 0
            msg = wintypes.MSG.from_address(int(message))
            if msg.message != WM_NCCALCSIZE or not msg.wParam or int(msg.hWnd or 0) not in self.windows:
                return False, 0
            user32 = ctypes.windll.user32
            if user32.IsZoomed(msg.hWnd):
                # maximised: Windows puts the frame's border outside the screen; the contents stay inside it
                params = _NCCALCSIZE_PARAMS.from_address(msg.lParam)
                dpi = user32.GetDpiForWindow(msg.hWnd) or 96
                padded = user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi)
                dx = user32.GetSystemMetricsForDpi(SM_CXSIZEFRAME, dpi) + padded
                dy = user32.GetSystemMetricsForDpi(SM_CYSIZEFRAME, dpi) + padded
                rect = params.rgrc[0]
                rect.left += dx; rect.right -= dx; rect.top += dy; rect.bottom -= dy
            return True, 0                      # the client area is the whole window: no system title bar or border
        except Exception:
            return False, 0


_filter: _Filter | None = None


def enable(app, window) -> bool:
    """Let Windows snap ``window`` (a frameless top-level QWindow) as it snaps a framed one. False where that does not apply."""
    global _filter
    if sys.platform != "win32":
        return False
    try:
        hwnd = int(window.winId())
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        if _filter is None:
            _filter = _Filter()
            app.installNativeEventFilter(_filter)
        _filter.windows.add(hwnd)
        style = user32.GetWindowLongPtrW(wintypes.HWND(hwnd), GWL_STYLE)
        user32.SetWindowLongPtrW(wintypes.HWND(hwnd), GWL_STYLE, style | SNAP_STYLES)
        user32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        return True
    except Exception:
        return False
