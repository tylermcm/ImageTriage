"""Use Windows' common-control reader-mode cursors without shipping copied artwork."""
from __future__ import annotations

import base64
import sys

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Property, Slot, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QPixmap


def _windows_cursors():
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    class IconInfo(ctypes.Structure):
        _fields_ = [("is_icon", wintypes.BOOL), ("x", wintypes.DWORD), ("y", wintypes.DWORD),
                    ("mask", wintypes.HBITMAP), ("color", wintypes.HBITMAP)]

    common = ctypes.WinDLL("comctl32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user.LoadImageW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p, wintypes.UINT,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.LoadImageW.restype = wintypes.HANDLE
    user.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(IconInfo)]
    user.GetIconInfo.restype = wintypes.BOOL
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteObject.restype = wintypes.BOOL
    result = {}
    # Comctl32 reader-mode resources: round origin, pan north, pan south.
    # LR_SHARED keeps cursor ownership with Windows; GetIconInfo bitmaps are ours.
    for direction, resource in ((0, 111), (-1, 112), (1, 113)):
        handle = user.LoadImageW(common._handle, resource, 2, 0, 0, 0x8040)
        if not handle:
            continue
        image = QImage.fromHICON(handle)
        info = IconInfo()
        if image.isNull() or not user.GetIconInfo(handle, ctypes.byref(info)):
            continue
        try:
            result[direction] = (image, int(info.x), int(info.y))
        finally:
            if info.mask:
                gdi.DeleteObject(info.mask)
            if info.color:
                gdi.DeleteObject(info.color)
    return result


class ScrollCursor(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            self._images = _windows_cursors()
        except (OSError, AttributeError):
            self._images = {}
        self._cursors = {key: QCursor(QPixmap.fromImage(image), x, y)
                         for key, (image, x, y) in self._images.items()}
        self._active = False
        self._direction = 0
        self._source = ""
        if 0 in self._images:
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.WriteOnly)
            self._images[0][0].save(buffer, "PNG")
            buffer.close()
            self._source = "data:image/png;base64," + base64.b64encode(bytes(data)).decode("ascii")
        QGuiApplication.instance().aboutToQuit.connect(self.end)

    anchorSource = Property(str, lambda self: self._source, constant=True)
    native = Property(bool, lambda self: len(self._images) == 3, constant=True)
    anchorWidth = Property(int, lambda self: self._images[0][0].width() if 0 in self._images else 0, constant=True)
    anchorHeight = Property(int, lambda self: self._images[0][0].height() if 0 in self._images else 0, constant=True)

    @Slot()
    def begin(self):
        if self._active:
            return
        self._direction = 0
        QGuiApplication.setOverrideCursor(self._cursors.get(0, QCursor(Qt.SizeVerCursor)))
        self._active = True

    @Slot(int)
    def direction(self, direction):
        direction = -1 if direction < 0 else 1 if direction > 0 else 0
        if self._active and direction != self._direction:
            self._direction = direction
            QGuiApplication.changeOverrideCursor(self._cursors.get(direction, self._cursors.get(0, QCursor(Qt.SizeVerCursor))))

    @Slot()
    def end(self):
        if self._active:
            QGuiApplication.restoreOverrideCursor()
            self._active = False
