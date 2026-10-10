"""The picture Image Triage paints in front of the embedded PhotoCraft window while it browses.

PhotoCraft is a separate process drawn into a native child window, so putting a photo on its canvas
means a cross-process round trip and a GPU document swap. Browsing must not wait for that: the popout
paints the photo itself, from its own caches and preloaded neighbours, in a native window stacked
above PhotoCraft's, and takes that window away once PhotoCraft is showing the same photo.

How much of PhotoCraft's window the picture covers is a setting:

``canvas``  only PhotoCraft's image area, so its panels stay visible (the default);
``full``    the whole embedded window; the panels appear when the photo has settled;
``off``     nothing: PhotoCraft draws every photo itself (the pre-overlay behaviour).

``canvas`` needs PhotoCraft's canvas rectangle (``ui.viewport``); until that is known, or if it is
unusable, the layer covers the whole window rather than guess.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget

REGION_CANVAS = "canvas"
REGION_FULL = "full"
REGION_OFF = "off"
REGIONS = (REGION_CANVAS, REGION_FULL, REGION_OFF)
DEFAULT_REGION = REGION_CANVAS
SETTINGS_KEY = "preview/photocraft_overlay_region"
# Launch the editor in its Camera Raw workspace (``photocraft --raw-workspace``): off until it has been
# used on real RAW files.
CAMERA_RAW_FIRST_KEY = "preview/photocraft_camera_raw_first"

# (label, value) for the Settings dialog, in the order they are offered.
REGION_CHOICES = (
    ("Image area only (editor panels stay visible)", REGION_CANVAS),
    ("Whole editor (panels appear once the photo settles)", REGION_FULL),
    ("Off (the editor draws every photo itself)", REGION_OFF),
)

# Smaller than this is noise from a half-laid-out window, not a canvas.
_MIN_SIDE = 64


def normalize_region(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in REGIONS else DEFAULT_REGION


def layer_rect(region: str, host_rect: QRect, viewport: dict | None, device_pixel_ratio: float) -> QRect:
    """Where the layer goes, in the coordinates of the widget that holds the PhotoCraft host.

    ``host_rect`` is the embedded window's rectangle there; ``viewport`` is PhotoCraft's ``ui.viewport``
    reply (physical pixels, relative to the window).
    """
    if region != REGION_CANVAS or not viewport:
        return QRect(host_rect)
    camera_raw = viewport.get("cameraRaw")
    camera_raw = camera_raw if isinstance(camera_raw, dict) else {}
    # An open Camera Raw dialog shows the photo in its own view; otherwise it is the editor's canvas.
    source = camera_raw.get("view") or camera_raw.get("preview") or viewport.get("canvas")
    try:
        x, y = float(source["x"]), float(source["y"])
        width, height = float(source["width"]), float(source["height"])
    except (KeyError, TypeError, ValueError):
        return QRect(host_rect)
    ratio = device_pixel_ratio if device_pixel_ratio > 0 else 1.0
    rect = QRect(
        host_rect.left() + round(x / ratio),
        host_rect.top() + round(y / ratio),
        round(width / ratio),
        round(height / ratio),
    ).intersected(host_rect)
    if rect.width() < _MIN_SIDE or rect.height() < _MIN_SIDE:
        return QRect(host_rect)
    return rect


def fit_rect(image_size: QSize, area: QSize) -> QRect:
    """``image_size`` scaled to fit inside ``area`` keeping its aspect ratio, centred (never upscaled
    past the area; a smaller image is scaled up to fit, as PhotoCraft's Fit on Screen does)."""
    if image_size.isEmpty() or area.isEmpty():
        return QRect()
    scale = min(area.width() / image_size.width(), area.height() / image_size.height())
    width = max(1, round(image_size.width() * scale))
    height = max(1, round(image_size.height() * scale))
    return QRect((area.width() - width) // 2, (area.height() - height) // 2, width, height)


class NativeImageLayer(QWidget):
    """A native window that paints one picture, fitted and centred on black."""

    clicked = Signal()
    # (path, placeholder): a frame carrying this photo's picture was painted. Emitted from the paint
    # handler so latency is measured to the pixels, not to the call that asked for them.
    painted = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # A real window of its own: only a native sibling can sit above PhotoCraft's native window.
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName("nativeImageLayer")
        self._image = QImage()
        self._path = ""
        self._placeholder = True
        self._background = QColor(0, 0, 0)
        self.hide()

    @property
    def path(self) -> str:
        return self._path

    def image_size(self) -> QSize:
        return self._image.size()

    def set_background(self, color: QColor) -> None:
        if color != self._background:
            self._background = QColor(color)
            self.update()

    def set_image(self, image: QImage, path: str = "", *, placeholder: bool = False) -> None:
        """Show ``image`` (null clears it to the background) for the photo at ``path``.

        ``placeholder`` marks a stand-in (the grid thumbnail) that a decoded picture will replace.
        """
        self._image = image if image is not None else QImage()
        self._path = path
        self._placeholder = placeholder
        self.update()

    def place(self, rect: QRect) -> None:
        if self.geometry() != rect:
            self.setGeometry(rect)

    def present(self, rect: QRect) -> None:
        """Position and show the layer above everything else in its parent."""
        self.place(rect)
        if not self.isVisible():
            self.show()
        self.raise_()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        event.accept()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(event.rect(), self._background)
        if self._image.isNull():
            return
        target = fit_rect(self._image.size(), self.size())
        if target.isEmpty():
            return
        # Smooth filtering only when the picture is actually resampled; a decode sized to the layer
        # (the common case) is a straight copy.
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, target.size() != self._image.size())
        painter.drawImage(target, self._image)
        painter.end()
        if self._path:
            self.painted.emit(self._path, self._placeholder)
