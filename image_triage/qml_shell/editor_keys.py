"""Route Space from the foreign PhotoCraft window without capturing other apps' keys."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal


class SpacePress:
    """Swallow a complete press, including repeats after focus returns to the grid."""

    def __init__(self, activated):
        self.activated = activated
        self.held = False

    def consume(self, key: int, down: bool, focused: bool, modified: bool) -> bool:
        if key != 0x20:
            return False
        if self.held:
            if not down:
                self.held = False
            return True
        if down and focused and not modified:
            self.held = True
            self.activated()
            return True
        return False


class EditorSpaceShortcut(QObject):
    pressed = Signal()

    def __init__(self, hwnd, parent=None):
        super().__init__(parent)
        self._hwnd = hwnd
        self._hook = None
        self._callback = None
        self._press = SpacePress(self.pressed.emit)

    def start(self) -> None:
        if sys.platform != "win32" or self._hook:
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        result_type = ctypes.c_ssize_t
        callback_type = ctypes.WINFUNCTYPE(result_type, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class Key(ctypes.Structure):
            _fields_ = [("vk", wintypes.DWORD), ("scan", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

        class ThreadInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                        *[(name, wintypes.HWND) for name in ("active", "focus", "capture", "menu", "move", "caret")],
                        ("rect", wintypes.RECT)]

        user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(ThreadInfo)]
        user32.GetGUIThreadInfo.restype = wintypes.BOOL
        user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
        user32.IsChild.restype = wintypes.BOOL
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = result_type
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HANDLE
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def receive(code, message, data):
            try:
                if code >= 0 and message in (0x100, 0x101, 0x104, 0x105):
                    key = ctypes.cast(data, ctypes.POINTER(Key)).contents.vk
                    hwnd = self._hwnd()
                    info = ThreadInfo()
                    info.cbSize = ctypes.sizeof(info)
                    focused = bool(hwnd and user32.GetGUIThreadInfo(0, ctypes.byref(info))
                                   and (info.focus == hwnd or user32.IsChild(hwnd, info.focus)))
                    modified = any(user32.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12, 0x5B, 0x5C))
                    if self._press.consume(key, message in (0x100, 0x104), focused, modified):
                        return 1
            except Exception:
                # Never break input delivery if the foreign window disappears.
                pass
            return user32.CallNextHookEx(None, code, message, data)

        self._callback = callback_type(receive)
        self._hook = user32.SetWindowsHookExW(13, self._callback, kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            raise ctypes.WinError(ctypes.get_last_error())
        self._user32 = user32

    def stop(self) -> None:
        if self._hook:
            if not self._user32.UnhookWindowsHookEx(self._hook):
                return  # Keep the callback alive if Windows still owns the hook.
            self._hook = None
            self._callback = None
