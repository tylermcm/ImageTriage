"""PocketDrop hosted in Qt.

PocketDrop's shared UI (native/pocketdrop/src/ui) lays itself out, paints and
handles clicks on its own, exactly as in the standalone app. This widget is its
host: it draws what PocketDrop asks for with QPainter, feeds it input, and
provides the clipboard, dialogs, menus and settings through Qt.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QFileInfo, QPointF, QRectF, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QFileIconProvider, QLabel, QMenu, QMessageBox, QSizePolicy, QVBoxLayout, QWidget

from . import _bridge as pd

SETTINGS_GROUP = "pocketdrop"
SLOW_TICK_MS = 500
FAST_TICK_MS = 33
# PocketDrop is designed at 385 DIPs wide and scales down to fit whatever
# width the Library pane has, so it sets no minimum of its own.
PREFERRED_WIDTH = 385

# Font enum order in src/ui/gfx.h: family role, semibold, size in DIPs.
_FONT_SPECS = (
    ("display", True, 16.5),   # Title
    ("ui", False, 13.5),       # Body
    ("ui", True, 13.5),        # BodyBold
    ("ui", False, 12.0),       # Small
    ("ui", True, 12.0),        # SmallBold
    ("mono", False, 12.0),     # Mono
    ("display", True, 19.0),   # Big
)
_FAMILIES = {
    "ui": ("Segoe UI Variable Text", "Segoe UI"),
    "display": ("Segoe UI Variable Display", "Segoe UI"),
    "mono": ("Cascadia Mono", "Consolas"),
}
_ALIGN = (Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignHCenter, Qt.AlignmentFlag.AlignRight)


def _qcolor(values) -> QColor:
    r, g, b, a = pd.floats(values, 4)
    return QColor.fromRgbF(r, g, b, a)


# PocketDrop's own page colour (BG in native/pocketdrop/src/ui/ui.cpp). The host
# swaps it for the window's pane colour so the page matches Library and Faces.
POCKETDROP_BG = QColor(0x0F, 0x0F, 0x13)


def _qrect(values) -> QRectF:
    left, top, right, bottom = pd.floats(values, 4)
    return QRectF(left, top, right - left, bottom - top)


def _build_fonts() -> list[QFont]:
    available = set(QFontDatabase.families())
    fonts = []
    for role, semibold, size in _FONT_SPECS:
        font = QFont()
        for family in _FAMILIES[role]:
            if family in available:
                font.setFamily(family)
                break
        else:
            if role == "mono":
                font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        # DIPs are 1/96 in; Qt points are 1/72 in at its logical 96 DPI.
        font.setPointSizeF(size * 0.75)
        font.setWeight(QFont.Weight.DemiBold if semibold else QFont.Weight.Normal)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        fonts.append(font)
    return fonts


class PocketDropView(QWidget):
    """The PocketDrop surface. Starts its server on first use (start())."""

    _woken = Signal()

    def __init__(self, lib: ctypes.CDLL, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pocketDropView")
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._lib = lib
        self._host: int | None = None
        self._painter: QPainter | None = None
        self._clips: list[QRectF] = []
        self._fonts = _build_fonts()
        self._metrics = [QFontMetricsF(font) for font in self._fonts]
        self._icons: dict[str, object] = {}
        self._icon_provider = QFileIconProvider()
        self._settings = QSettings()
        # The page colour PocketDrop's background is drawn in; see set_background.
        self._background = QColor(POCKETDROP_BG)

        self._slow = QTimer(self)
        self._slow.setInterval(SLOW_TICK_MS)
        self._slow.timeout.connect(self._tick)
        self._fast = QTimer(self)
        self._fast.setInterval(FAST_TICK_MS)
        self._fast.timeout.connect(self._tick)
        # wake() arrives on PocketDrop's worker threads; the queued signal
        # brings the posted work back to this thread.
        self._woken.connect(self._run_posted, Qt.ConnectionType.QueuedConnection)

        self._gfx = pd.Gfx(
            None,
            pd.CLEAR(self._g_clear),
            pd.FILL_RECT(self._g_fill_rect),
            pd.FILL_ROUND(self._g_fill_round),
            pd.STROKE_ROUND(self._g_stroke_round),
            pd.FILL_CIRCLE(self._g_fill_circle),
            pd.GRADIENT_ROUND(self._g_gradient_round),
            pd.STROKE_POLYLINE(self._g_stroke_polyline),
            pd.TEXT(self._g_text),
            pd.MEASURE(self._g_measure),
            pd.SET_ALIASED(self._g_set_aliased),
            pd.PUSH_CLIP(self._g_push_clip),
            pd.POP_CLIP(self._g_pop_clip),
            pd.FILE_ICON(self._g_file_icon),
        )
        self._shell = pd.Shell(
            None,
            pd.VOID(lambda _ctx: self.update()),
            pd.SET_FLAG(self._s_set_animating),
            pd.VOID(lambda _ctx: self._woken.emit()),
            pd.CLIENT_SIZE(self._s_client_size),
            pd.WITH_TEXT(lambda _ctx, value: QGuiApplication.clipboard().setText(pd.text(value))),
            pd.COPY_IMAGE(self._s_copy_image),
            pd.INTO_SINK(self._s_read_clipboard),
            pd.BROWSE(self._s_browse),
            pd.TEXT_INTO_SINK(self._s_choose_folder),
            pd.WITH_TEXT(lambda _ctx, url: QDesktopServices.openUrl(QUrl(pd.text(url)))),
            pd.VOID(self._s_open_bluetooth_setup),
            pd.CONFIRM(self._s_confirm_anywhere_risk),
            pd.WITH_TEXT(lambda _ctx, path: reveal_in_file_manager(pd.text(path))),
            pd.VOID(lambda _ctx: QApplication.alert(self.window())),
            pd.POPUP_MENU(self._s_popup_menu),
            pd.ALERT(lambda _ctx, title, message: QMessageBox.information(self, pd.text(title), pd.text(message))),
            pd.LOAD_SETTING(self._s_load_setting),
            pd.SAVE_SETTING(self._s_save_setting),
            pd.TEXT_INTO_SINK(self._s_load_string),
            pd.SAVE_STRING(self._s_save_string),
            pd.SET_FLAG(self._s_set_topmost),
            pd.TEXT_INTO_SINK(self._s_clean_path),
            pd.NATIVE_WINDOW(lambda _ctx: int(self.window().winId())),
        )
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    # -- lifecycle -----------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._host is not None

    def start(self) -> None:
        if self._host is not None:
            return
        host = self._lib.pd_create(ctypes.byref(self._shell), ctypes.byref(self._gfx))
        if not host:
            return
        self._host = host
        self._lib.pd_start(host)
        self._slow.start()
        self.update()

    def shutdown(self) -> None:
        host, self._host = self._host, None
        self._slow.stop()
        self._fast.stop()
        if host:
            self._lib.pd_destroy(host)

    def add_paths(self, paths: list[str]) -> None:
        paths = [path for path in paths if path]
        if not paths:
            return
        self.start()
        if self._host is None:
            return
        array = (ctypes.c_char_p * len(paths))(*(pd.encode(path) for path in paths))
        self._lib.pd_add_paths(self._host, array, len(paths))

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(PREFERRED_WIDTH, 750)

    def _tick(self) -> None:
        if self._host:
            self._lib.pd_tick(self._host)

    def _run_posted(self) -> None:
        if self._host:
            self._lib.pd_run_posted(self._host)

    # -- Qt events -------------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.start()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if not self._host:
            painter = QPainter(self)
            painter.fillRect(self.rect(), self._background)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self._painter, self._clips = painter, []
        try:
            self._lib.pd_paint(self._host, float(self.devicePixelRatioF()))
        finally:
            self._painter = None
            painter.end()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._host:
            pos = event.position()
            self._lib.pd_mouse_move(self._host, pos.x(), pos.y())
            self._sync_cursor()

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        super().leaveEvent(event)
        if self._host:
            self._lib.pd_mouse_leave(self._host)
            self._sync_cursor()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self._host and event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            pos = event.position()
            self._lib.pd_mouse_down(self._host, pos.x(), pos.y())

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._host and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            self._lib.pd_mouse_up(self._host, pos.x(), pos.y())
            self._sync_cursor()

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self._host:
            self._lib.pd_wheel(self._host, event.angleDelta().y() / 120.0)
            event.accept()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if self._host and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            key = event.key()
            if key == Qt.Key.Key_V:
                self._lib.pd_paste(self._host)
                return
            if key == Qt.Key.Key_O:
                self._lib.pd_browse(self._host, 0)
                return
            if key == Qt.Key.Key_C:
                self._lib.pd_copy_link(self._host)
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        mime = event.mimeData()
        usable = bool(_local_paths(mime)) or mime.hasText()
        if usable:
            self.start()
            event.acceptProposedAction()
        if self._host:
            self._lib.pd_set_drag_over(self._host, 1 if usable else 0)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        if self._host:
            self._lib.pd_set_drag_over(self._host, 0)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if not self._host:
            return
        self._lib.pd_set_drag_over(self._host, 0)
        mime = event.mimeData()
        paths = _local_paths(mime)
        if paths:
            self.add_paths(paths)
        elif mime.hasText():
            self._lib.pd_add_text(self._host, pd.encode(mime.text()))
        event.acceptProposedAction()

    def _sync_cursor(self) -> None:
        pointer = bool(self._host) and bool(self._lib.pd_wants_pointer(self._host))
        self.setCursor(Qt.CursorShape.PointingHandCursor if pointer else Qt.CursorShape.ArrowCursor)

    # -- page colour -----------------------------------------------------------

    def set_background(self, color: QColor) -> None:
        """Draw PocketDrop's page in ``color`` instead of its own near-black.

        Every paint in PocketDrop's background colour is swapped, not just the
        clear: it also rings the "received" badges in it so they look cut out
        of the page, and those rings have to follow or they show as outlines.
        """
        color = QColor(color)
        if color == self._background:
            return
        self._background = color
        self.update()

    def _color(self, values) -> QColor:
        color = _qcolor(values)
        if color.rgb() == POCKETDROP_BG.rgb():
            swapped = QColor(self._background)
            swapped.setAlphaF(color.alphaF())
            return swapped
        return color

    # -- pd_gfx ----------------------------------------------------------------

    def _g_clear(self, _ctx, color) -> None:
        self._painter.fillRect(self.rect(), self._color(color))

    def _g_fill_rect(self, _ctx, rect, color) -> None:
        self._painter.fillRect(_qrect(rect), self._color(color))

    def _g_fill_round(self, _ctx, rect, radius, color) -> None:
        p = self._painter
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color(color))
        p.drawRoundedRect(_qrect(rect), radius, radius)

    def _g_stroke_round(self, _ctx, rect, radius, color, width, dashed) -> None:
        p = self._painter
        pen = QPen(self._color(color), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if dashed:
            # PocketDrop's dashes are 2.5 on, 3 off, in stroke widths.
            pen.setDashPattern([2.5, 3.0])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        half = width / 2
        p.drawRoundedRect(_qrect(rect).adjusted(half, half, -half, -half), radius, radius)

    def _g_fill_circle(self, _ctx, x, y, radius, color) -> None:
        p = self._painter
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color(color))
        p.drawEllipse(QPointF(x, y), radius, radius)

    def _g_gradient_round(self, _ctx, rect, radius, start, end) -> None:
        area = _qrect(rect)
        gradient = QLinearGradient(area.topLeft(), area.bottomRight())
        gradient.setColorAt(0.0, self._color(start))
        gradient.setColorAt(1.0, self._color(end))
        p = self._painter
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient)
        p.drawRoundedRect(area, radius, radius)

    def _g_stroke_polyline(self, _ctx, points, count, closed, color, width) -> None:
        xy = pd.floats(points, count * 2)
        path = QPainterPath(QPointF(xy[0], xy[1]))
        for index in range(1, count):
            path.lineTo(xy[index * 2], xy[index * 2 + 1])
        if closed:
            path.closeSubpath()
        pen = QPen(self._color(color), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p = self._painter
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    def _g_text(self, _ctx, value, rect, font, color, align) -> None:
        area = _qrect(rect)
        metrics = self._metrics[font]
        content = metrics.elidedText(pd.text(value), Qt.TextElideMode.ElideRight, max(0.0, area.width()))
        p = self._painter
        p.setFont(self._fonts[font])
        p.setPen(self._color(color))
        p.drawText(area, int(_ALIGN[align] | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine), content)

    def _g_measure(self, _ctx, value, font) -> float:
        return float(self._metrics[font].horizontalAdvance(pd.text(value)))

    def _g_set_aliased(self, _ctx, aliased) -> None:
        self._painter.setRenderHint(QPainter.RenderHint.Antialiasing, not aliased)

    def _g_push_clip(self, _ctx, rect) -> None:
        area = _qrect(rect)
        if self._clips:
            area = area.intersected(self._clips[-1])
        self._clips.append(area)
        self._painter.setClipRect(area)

    def _g_pop_clip(self, _ctx) -> None:
        if self._clips:
            self._clips.pop()
        if self._clips:
            self._painter.setClipRect(self._clips[-1])
        else:
            self._painter.setClipping(False)

    def _g_file_icon(self, _ctx, path, rect) -> None:
        key = pd.text(path)
        icon = self._icons.get(key)
        if icon is None:
            icon = self._icon_provider.icon(QFileInfo(key))
            self._icons[key] = icon
        area = _qrect(rect)
        side = max(1, round(max(area.width(), area.height())))
        pixmap = icon.pixmap(QSize(side, side), self.devicePixelRatioF())
        self._painter.drawPixmap(area, pixmap, QRectF(pixmap.rect()))

    # -- pd_shell --------------------------------------------------------------

    def _s_set_animating(self, _ctx, on) -> None:
        if on:
            self._fast.start()
        else:
            self._fast.stop()

    def _s_client_size(self, _ctx, width, height) -> None:
        width[0] = float(self.width())
        height[0] = float(self.height())

    def _s_copy_image(self, _ctx, rgba, width, height) -> int:
        data = ctypes.string_at(rgba, width * height * 4)
        image = QImage(data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        QGuiApplication.clipboard().setImage(image)
        return 1

    def _s_read_clipboard(self, _ctx, sink) -> None:
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None:
            return
        paths = _local_paths(mime)
        if paths:
            for path in paths:
                self._lib.pd_sink_add(sink, pd.encode(path))
            return
        if mime.hasImage():
            image = QGuiApplication.clipboard().image()
            if not image.isNull():
                folder = _pasted_images_dir()
                name = datetime.now().strftime("Pasted image %Y-%m-%d %H.%M.%S.png")
                target = folder / name
                if image.save(str(target), "PNG"):
                    self._lib.pd_sink_add(sink, pd.encode(str(target)))
                    return
        if mime.hasText():
            self._lib.pd_sink_set(sink, pd.encode(mime.text()))

    def _s_browse(self, _ctx, folders, sink) -> None:
        if folders:
            chosen = QFileDialog.getExistingDirectory(self, "Choose a folder to share")
            chosen_paths = [chosen] if chosen else []
        else:
            chosen_paths, _filter = QFileDialog.getOpenFileNames(self, "Choose files to share")
        for path in chosen_paths:
            self._lib.pd_sink_add(sink, pd.encode(os.path.normpath(path)))

    def _s_choose_folder(self, _ctx, title, sink) -> None:
        chosen = QFileDialog.getExistingDirectory(self, pd.text(title))
        if chosen:
            self._lib.pd_sink_set(sink, pd.encode(os.path.normpath(chosen)))

    def _s_open_bluetooth_setup(self, _ctx) -> None:
        if sys.platform == "win32":
            subprocess.Popen(["explorer.exe", "shell:::{A8A91A66-3A7D-4424-8D24-04E180695C7A}"])

    def _s_confirm_anywhere_risk(self, _ctx, dont_show) -> int:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("PocketDrop")
        box.setText("Anyone with this link can download the shared files")
        box.setInformativeText(
            "The link remains active until it expires, you create a new link, leave Anywhere mode, or close "
            "Image Triage. Sending files back to this PC is protected by a separate receive code shown only in "
            "PocketDrop."
        )
        proceed = box.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(proceed)
        check = QCheckBox("Don't show this warning again", box)
        box.setCheckBox(check)
        box.exec()
        dont_show[0] = 1 if check.isChecked() else 0
        return 1 if box.clickedButton() is proceed else 0

    def _s_popup_menu(self, _ctx, items_json, x, y) -> int:
        try:
            items = json.loads(pd.text(items_json))
        except ValueError:
            return 0
        menu = QMenu(self)
        _fill_menu(menu, items)
        # PocketDrop anchors the menu's top-right corner at (x, y).
        anchor = self.mapToGlobal(QPointF(x, y).toPoint())
        anchor.setX(anchor.x() - menu.sizeHint().width())
        chosen = menu.exec(anchor)
        return int(chosen.data()) if chosen is not None and chosen.data() else 0

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
        # Win32 rather than a Qt window flag: changing flags would recreate the
        # frameless main window and lose its native frame.
        if sys.platform != "win32":
            return
        hwnd = int(self.window().winId())
        topmost, not_topmost = -1, -2
        flags = 0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
        ctypes.windll.user32.SetWindowPos(hwnd, topmost if on else not_topmost, 0, 0, 0, 0, flags)

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


class PocketDropPanel(QWidget):
    """The Library panel page for PocketDrop, or an explanation when the
    native module isn't available."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pocketDropPanel")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view: PocketDropView | None = None
        self.error = ""
        try:
            lib = pd.load_library()
        except pd.NativeUnavailable as exc:
            self.error = str(exc)
            notice = QLabel(f"PocketDrop isn't available.\n\n{self.error}", self)
            notice.setObjectName("pocketDropUnavailable")
            notice.setWordWrap(True)
            notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(notice)
            return
        self.view = PocketDropView(lib, self)
        layout.addWidget(self.view)

    @property
    def available(self) -> bool:
        return self.view is not None

    def set_background(self, color: QColor) -> None:
        """Match the page to the window's other panes (see PocketDropView)."""
        if self.view is not None:
            self.view.set_background(color)

    def add_paths(self, paths: list[str]) -> bool:
        if self.view is None:
            return False
        self.view.add_paths(paths)
        return True

    def shutdown(self) -> None:
        if self.view is not None:
            self.view.shutdown()


def _fill_menu(menu: QMenu, items: list[dict]) -> None:
    for item in items:
        if item.get("separator"):
            menu.addSeparator()
            continue
        submenu = item.get("submenu") or []
        if submenu:
            _fill_menu(menu.addMenu(item.get("label", "")), submenu)
            continue
        action = menu.addAction(item.get("label", ""))
        action.setData(int(item.get("id", 0)))
        action.setEnabled(bool(item.get("enabled", True)))
        if item.get("checked"):
            action.setCheckable(True)
            action.setChecked(True)


def _local_paths(mime) -> list[str]:
    if mime is None or not mime.hasUrls():
        return []
    return [os.path.normpath(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]


def _pasted_images_dir() -> Path:
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
