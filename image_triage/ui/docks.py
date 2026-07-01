from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from PySide6.QtCore import QByteArray, QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCloseEvent, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QSizeGrip,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..ai_results import build_ai_explanation_lines
from ..review_tools import EMPTY_INSPECTION_STATS, InspectionStats
from ..review_workflows import review_round_label
from ..quality.poi import focus_poi, should_use_smart_focus_crop
from .theme import ThemePalette, default_theme

if TYPE_CHECKING:
    from ..ai_results import AIImageResult
    from ..metadata import CaptureMetadata
    from ..models import ImageRecord, SessionAnnotation


TAB_WIDTH = 34
INSPECTOR_PREVIEW_COLLAPSED_HEIGHT = 40


class InspectorHistogram(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stats = EMPTY_INSPECTION_STATS
        self.setObjectName("inspectorHistogram")
        self.setMinimumHeight(54)
        self.setMaximumHeight(70)

    def set_stats(self, stats: InspectionStats | None) -> None:
        self._stats = stats or EMPTY_INSPECTION_STATS
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(rect, QColor(18, 20, 24))
        painter.setPen(QPen(QColor(54, 58, 66), 1))
        painter.drawRoundedRect(rect, 5, 5)

        histogram = self._stats.histogram_luma
        max_value = max(histogram) if histogram else 0
        if max_value <= 0:
            painter.setPen(QColor(126, 132, 144))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Histogram not available")
            return

        plot = rect.adjusted(6, 5, -6, -5)
        width = max(1, plot.width())
        height = max(1, plot.height())
        painter.setPen(QPen(QColor(162, 172, 190), 1))
        for x in range(width):
            start = int((x / width) * len(histogram))
            end = max(start + 1, int(((x + 1) / width) * len(histogram)))
            value = max(histogram[start:end])
            bar_height = int((value / max_value) * height)
            painter.drawLine(plot.left() + x, plot.bottom(), plot.left() + x, plot.bottom() - bar_height)


class SnapPreviewOverlay(QWidget):
    def __init__(self, parent=None) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(parent, flags)
        self.setObjectName("snapPreviewOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def show_global_rect(self, rect: QRect | None) -> None:
        if rect is None or rect.isNull() or not rect.isValid():
            self.hide()
            return
        self.setGeometry(rect)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        painter.fillPath(path, QColor(43, 126, 255, 72))
        painter.setPen(QPen(QColor(96, 170, 255, 230), 2))
        painter.drawPath(path)


def _encode_qbytearray(value: QByteArray) -> str:
    if value.isEmpty():
        return ""
    return bytes(value.toBase64()).decode("ascii")


def _decode_qbytearray(value: str | None) -> QByteArray:
    if not value:
        return QByteArray()
    return QByteArray.fromBase64(value.encode("ascii"))


class WorkspaceSideTab(QToolButton):
    def __init__(self, title: str, side: str, variant: str, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._side = side
        self._variant = variant
        self._theme = default_theme()
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(f"Show {title}")
        self.setFixedSize(TAB_WIDTH, 148)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:
        return QSize(TAB_WIDTH, 148)

    def set_side(self, side: str) -> None:
        self._side = side
        self.update()

    def paintEvent(self, event) -> None:
        theme = self._theme
        body_color = theme.raised_bg if self._variant == "library" else theme.input_bg
        fill = theme.input_hover_bg if self._hovered else body_color
        text = theme.text_primary if self._hovered else theme.text_secondary

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect()).adjusted(3, 4, -3, -4)
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)
        painter.fillPath(path, fill.qcolor())
        pen = QPen(theme.border.qcolor(), 1)
        painter.setPen(pen)
        painter.drawPath(path)

        painter.setPen(text.qcolor())
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(-90 if self._side == "left" else 90)
        text_rect = QRectF(-self.height() / 2, -self.width() / 2, self.height(), self.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._title)


class WorkspacePanelHeader(QWidget):
    collapse_requested = Signal()
    popout_requested = Signal()
    close_requested = Signal()
    drag_popout_requested = Signal(object, object)
    drag_move_requested = Signal(object)
    drag_release_requested = Signal(object)

    def __init__(self, title: str, subtitle: str, *, variant: str, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._floating = False
        self._drag_offset: QPoint | None = None
        self._drag_start: QPoint | None = None
        self._dragging = False
        self.setObjectName(f"{variant}PanelHeader")
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 8, 7)
        layout.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("paneTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_row.addWidget(self.title_label)
        title_row.addStretch(1)

        self.minimize_button = self._build_button("\u2212", "Collapse panel", "workspacePanelButton")
        self.maximize_button = self._build_button("\u25A1", "Pop out panel", "workspacePanelButton")
        self.close_button = self._build_button("\u2715", "Hide panel", "workspacePanelCloseButton")

        self.minimize_button.clicked.connect(lambda _checked=False: self.collapse_requested.emit())
        self.maximize_button.clicked.connect(lambda _checked=False: self.popout_requested.emit())
        self.close_button.clicked.connect(lambda _checked=False: self.close_requested.emit())

        title_row.addWidget(self.minimize_button)
        title_row.addWidget(self.maximize_button)
        title_row.addWidget(self.close_button)
        layout.addLayout(title_row)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("panelHeaderSubtitle")
        self.subtitle_label.setWordWrap(False)
        self.subtitle_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.subtitle_label)

        self._sync_tooltips()

    def _build_button(self, text: str, tooltip: str, object_name: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName(object_name)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedSize(24, 24)
        return button

    def set_floating_state(self, floating: bool, *, maximized: bool = False) -> None:
        self._floating = floating
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._sync_tooltips()

    def _sync_tooltips(self) -> None:
        if self._floating:
            self.minimize_button.setText("\u00AB")
            self.maximize_button.setText("\u21A9")
            self.minimize_button.setToolTip(f"Collapse {self._title} palette")
            self.maximize_button.setToolTip(f"Dock {self._title} back into the workspace")
            self.close_button.setToolTip(f"Hide {self._title}")
            return
        self.minimize_button.setText("\u2212")
        self.maximize_button.setText("\u25A1")
        self.minimize_button.setToolTip(f"Collapse {self._title}")
        self.maximize_button.setToolTip(f"Pop out {self._title}")
        self.close_button.setToolTip(f"Hide {self._title}")

    def _floating_palette_window(self) -> QWidget:
        widget = self.parentWidget()
        while widget is not None:
            if widget.objectName() == "floatingPanelPalette":
                return widget
            widget = widget.parentWidget()
        return self.window()

    def start_floating_drag(self, global_pos: QPoint) -> None:
        top_level = self._floating_palette_window()
        self._drag_offset = global_pos - top_level.frameGeometry().topLeft()
        self._drag_start = global_pos
        self._dragging = True

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._floating:
                self.start_floating_drag(event.globalPosition().toPoint())
            else:
                self._drag_offset = event.position().toPoint()
            self._drag_start = event.globalPosition().toPoint()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._floating and self._dragging and self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            top_level = self._floating_palette_window()
            current = event.globalPosition().toPoint()
            top_level.move(current - self._drag_offset)
            self.drag_move_requested.emit(current)
            event.accept()
            return
        if self._floating and self._drag_offset is not None and self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current = event.globalPosition().toPoint()
            if not self._dragging and (current - self._drag_start).manhattanLength() < QApplication.startDragDistance():
                event.accept()
                return
            self._dragging = True
            top_level = self._floating_palette_window()
            top_level.move(current - self._drag_offset)
            self.drag_move_requested.emit(current)
            event.accept()
            return
        if not self._floating and self._drag_offset is not None and self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current = event.globalPosition().toPoint()
            if not self._dragging and (current - self._drag_start).manhattanLength() < QApplication.startDragDistance():
                event.accept()
                return
            self._dragging = True
            self.drag_popout_requested.emit(current, self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._floating and self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._drag_offset = None
            self._drag_start = None
            self.drag_release_requested.emit(event.globalPosition().toPoint())
            event.accept()
            return
        self._dragging = False
        self._drag_offset = None
        self._drag_start = None
        super().mouseReleaseEvent(event)


class FloatingPanelWindow(QWidget):
    close_requested = Signal()
    maximized_changed = Signal(bool)

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(title)
        self.setObjectName("floatingPanelPalette")
        self.setMinimumSize(260, 220)
        self.resize(360, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._splitter.setObjectName("floatingPanelSplitter")
        self._splitter.setChildrenCollapsible(False)
        layout.addWidget(self._splitter, 1)

        grip_row = QWidget(self)
        grip_row.setObjectName("floatingPanelResizeRow")
        grip_layout = QHBoxLayout(grip_row)
        grip_layout.setContentsMargins(0, 0, 2, 2)
        grip_layout.setSpacing(0)
        grip_layout.addStretch(1)
        self._size_grip = QSizeGrip(grip_row)
        self._size_grip.setObjectName("floatingPanelSizeGrip")
        grip_layout.addWidget(self._size_grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(grip_row)

    def set_card(self, key: str, title: str, widget: QWidget, *, target_key: str | None = None) -> None:
        existing_index = self.index_for_key(key)
        if existing_index >= 0:
            existing = self.take_card(key)
            if existing is not None and existing is not widget:
                existing.setParent(None)
        widget.setProperty("workspacePanelKey", key)
        host = self._host_for_key(target_key) if target_key else None
        if host is None:
            host = self._first_host() or self._create_host()
        host.addTab(widget, title)
        host.setCurrentWidget(widget)
        self._sync_hosts()

    def split_card(self, key: str, title: str, widget: QWidget, *, target_key: str | None, placement: str) -> None:
        existing = self.take_card(key)
        if existing is not None and existing is not widget:
            existing.setParent(None)
        target_host = self._host_for_key(target_key) or self._first_host()
        target_index = self._host_index(target_host)
        insert_index = self._splitter.count()
        if target_index >= 0:
            insert_index = target_index if placement in {"top", "left"} else target_index + 1
        host = self._create_host(insert_index=insert_index)
        widget.setProperty("workspacePanelKey", key)
        host.addTab(widget, title)
        host.setCurrentWidget(widget)
        self._sync_tab_bar()
        self._equalize_split_sizes()

    def take_card(self, key: str | None = None) -> QWidget | None:
        if self._splitter.count() == 0:
            return None
        host = self._host_for_key(key) if key is not None else self._first_host()
        if host is None:
            return None
        index = self._index_in_host(host, key) if key is not None else host.currentIndex()
        if index < 0:
            return None
        widget = host.widget(index)
        host.removeTab(index)
        if widget is not None:
            widget.setParent(None)
        self._remove_empty_hosts()
        self._sync_hosts()
        return widget

    def contains_key(self, key: str) -> bool:
        return self.index_for_key(key) >= 0

    def index_for_key(self, key: str | None) -> int:
        host = self._host_for_key(key)
        if host is not None:
            return self._index_in_host(host, key)
        return -1

    def keys(self) -> tuple[str, ...]:
        keys: list[str] = []
        for host in self._hosts():
            for index in range(host.count()):
                widget = host.widget(index)
                key = str(widget.property("workspacePanelKey") or "") if widget is not None else ""
                if key:
                    keys.append(key)
        return tuple(keys)

    def card_count(self) -> int:
        return sum(host.count() for host in self._hosts())

    def key_at_global_pos(self, global_pos: QPoint) -> str | None:
        host = self.host_at_global_pos(global_pos)
        if host is None:
            return None
        widget = host.currentWidget()
        return str(widget.property("workspacePanelKey") or "") if widget is not None else None

    def host_at_global_pos(self, global_pos: QPoint) -> QTabWidget | None:
        for host in self._hosts():
            host_rect = QRect(host.mapToGlobal(QPoint(0, 0)), host.size())
            if host_rect.contains(global_pos):
                return host
        return None

    def _create_host(self, *, insert_index: int | None = None) -> QTabWidget:
        host = QTabWidget(self._splitter)
        host.setObjectName("floatingPanelTabs")
        host.setDocumentMode(True)
        host.setMovable(True)
        if insert_index is None or insert_index >= self._splitter.count():
            self._splitter.addWidget(host)
        else:
            self._splitter.insertWidget(max(0, insert_index), host)
        return host

    def _hosts(self) -> tuple[QTabWidget, ...]:
        hosts: list[QTabWidget] = []
        for index in range(self._splitter.count()):
            widget = self._splitter.widget(index)
            if isinstance(widget, QTabWidget):
                hosts.append(widget)
        return tuple(hosts)

    def _first_host(self) -> QTabWidget | None:
        hosts = self._hosts()
        return hosts[0] if hosts else None

    def _host_for_key(self, key: str | None) -> QTabWidget | None:
        if key is None:
            return None
        for host in self._hosts():
            if self._index_in_host(host, key) >= 0:
                return host
        return None

    def _host_index(self, host: QTabWidget | None) -> int:
        if host is None:
            return -1
        for index in range(self._splitter.count()):
            if self._splitter.widget(index) is host:
                return index
        return -1

    def _index_in_host(self, host: QTabWidget, key: str | None) -> int:
        if key is None:
            return -1
        for index in range(host.count()):
            widget = host.widget(index)
            if widget is not None and widget.property("workspacePanelKey") == key:
                return index
        return -1

    def _remove_empty_hosts(self) -> None:
        for host in self._hosts():
            if host.count() == 0:
                host.setParent(None)
                host.deleteLater()

    def _sync_hosts(self) -> None:
        self._remove_empty_hosts()
        self._sync_tab_bar()

    def _sync_tab_bar(self) -> None:
        for host in self._hosts():
            host.tabBar().setVisible(host.count() > 1)

    def _equalize_split_sizes(self) -> None:
        count = self._splitter.count()
        if count <= 1:
            return
        self._splitter.setSizes([1 for _ in range(count)])

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            self.maximized_changed.emit(self.isMaximized())
        super().changeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.close_requested.emit()
        event.ignore()


class WorkspacePanel(QWidget):
    collapse_requested = Signal(str)
    popout_requested = Signal(str)
    close_requested = Signal(str)
    expand_requested = Signal(str)
    drag_popout_requested = Signal(str, object, object)
    drag_move_requested = Signal(str, object)
    dock_drop_requested = Signal(str, object)

    def __init__(
        self,
        key: str,
        *,
        title: str,
        subtitle: str,
        side: str,
        variant: str,
        content: QWidget,
        preferred_width: int,
        minimum_width: int = 0,
        maximum_width: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.title = title
        self.side = side
        self.variant = variant
        self._expanded_width = preferred_width
        self._minimum_expanded_width = minimum_width if minimum_width > 0 else max(236, preferred_width - 44)
        self._maximum_expanded_width = maximum_width if maximum_width > 0 else preferred_width + 180
        self._mode = "expanded"

        self.setObjectName(f"{variant}PanelSlot")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.frame = QWidget(self)
        self.frame.setObjectName(f"{variant}WorkspacePanel")
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        self.header = WorkspacePanelHeader(title, subtitle, variant=variant, parent=self.frame)
        self.header.collapse_requested.connect(lambda: self.collapse_requested.emit(self.key))
        self.header.popout_requested.connect(lambda: self.popout_requested.emit(self.key))
        self.header.close_requested.connect(lambda: self.close_requested.emit(self.key))
        self.header.drag_popout_requested.connect(lambda point, offset: self.drag_popout_requested.emit(self.key, point, offset))
        self.header.drag_move_requested.connect(lambda point: self.drag_move_requested.emit(self.key, point))
        self.header.drag_release_requested.connect(lambda point: self.dock_drop_requested.emit(self.key, point))
        frame_layout.addWidget(self.header)

        self.viewport = QWidget(self.frame)
        self.viewport.setObjectName(f"{variant}PanelViewport")
        viewport_layout = QVBoxLayout(self.viewport)
        viewport_layout.setContentsMargins(0, 0, 0, 0)
        viewport_layout.setSpacing(0)
        viewport_layout.addWidget(content)
        frame_layout.addWidget(self.viewport, 1)

        self.tab_container = QWidget(self)
        tab_layout = QVBoxLayout(self.tab_container)
        self._tab_layout = tab_layout
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)
        self.side_tab = WorkspaceSideTab(title, side, variant, self.tab_container)
        self.side_tab.clicked.connect(lambda _checked=False: self.expand_requested.emit(self.key))
        tab_layout.addWidget(self.side_tab, 0, self._tab_alignment())
        tab_layout.addStretch(1)

        outer_layout.addWidget(self.frame, 1)
        outer_layout.addWidget(self.tab_container, 1)
        self.tab_container.hide()
        self._apply_expanded_constraints()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def expanded_width(self) -> int:
        return self._expanded_width

    @property
    def minimum_expanded_width(self) -> int:
        return self._minimum_expanded_width

    @property
    def maximum_expanded_width(self) -> int:
        return self._maximum_expanded_width

    def set_expanded_width(self, width: int) -> None:
        if width > 120:
            self._expanded_width = max(self._minimum_expanded_width, min(width, self._maximum_expanded_width))

    def apply_theme(self, theme: ThemePalette) -> None:
        self.side_tab.apply_theme(theme)

    def show_expanded(self) -> None:
        self._mode = "expanded"
        self._apply_expanded_constraints()
        self.tab_container.hide()
        self.frame.show()
        self.header.set_floating_state(False)
        self.show()

    def show_collapsed(self) -> None:
        self._mode = "collapsed"
        self.frame.hide()
        self.tab_container.show()
        self.setMinimumWidth(TAB_WIDTH)
        self.setMaximumWidth(TAB_WIDTH)
        self.show()

    def hide_panel(self) -> None:
        self._mode = "hidden"
        self.frame.hide()
        self.tab_container.hide()
        self._apply_expanded_constraints()
        self.hide()

    def set_floating(self) -> None:
        self._mode = "floating"
        self.header.set_floating_state(True)
        self.hide()

    def detach_frame(self) -> QWidget:
        if self.frame.parent() is self:
            layout = self.layout()
            if layout is not None:
                layout.removeWidget(self.frame)
        self.frame.setParent(None)
        return self.frame

    def attach_frame(self, frame: QWidget | None = None) -> None:
        target = frame or self.frame
        if target.parent() is self:
            return
        target.setParent(self)
        layout = self.layout()
        if layout is not None:
            layout.insertWidget(0, target, 1)
        self.frame = target

    def set_floating_state(self, floating: bool, *, maximized: bool = False) -> None:
        self.header.set_floating_state(floating, maximized=maximized)

    def set_side(self, side: str) -> None:
        self.side = side
        self.side_tab.set_side(side)
        self._tab_layout.setAlignment(self.side_tab, self._tab_alignment())

    def _apply_expanded_constraints(self) -> None:
        self.setMinimumWidth(self._minimum_expanded_width)
        self.setMaximumWidth(self._maximum_expanded_width)

    def _tab_alignment(self) -> Qt.AlignmentFlag:
        alignment = Qt.AlignmentFlag.AlignTop
        alignment |= Qt.AlignmentFlag.AlignLeft if self.side == "left" else Qt.AlignmentFlag.AlignRight
        return alignment


class WorkspaceDocks:
    def __init__(self, shell: QWidget, splitter: QSplitter, library: WorkspacePanel, inspector: WorkspacePanel) -> None:
        self.shell = shell
        self.splitter = splitter
        self.center = splitter.widget(1)
        self.library = library
        self.inspector = inspector
        self.toggle_actions: dict[str, QAction] = {}
        self._floating_windows: dict[str, FloatingPanelWindow] = {}
        self._snap_preview = SnapPreviewOverlay(self.shell.window())
        self._snap_distance = 28
        self._panel_map = {
            "library": self.library,
            "inspector": self.inspector,
        }
        self._side_columns = {
            "left": QSplitter(Qt.Orientation.Vertical, self.splitter),
            "right": QSplitter(Qt.Orientation.Vertical, self.splitter),
        }
        self._side_columns["left"].setObjectName("leftDockedPanelColumn")
        self._side_columns["right"].setObjectName("rightDockedPanelColumn")
        for column in self._side_columns.values():
            column.setChildrenCollapsible(False)
        self._side_orders = {
            "left": ["library"],
            "right": ["inspector"],
        }
        self._side_tab_groups: dict[str, list[str]] = {"left": [], "right": []}
        self._side_tab_widgets = {
            "left": QTabWidget(self._side_columns["left"]),
            "right": QTabWidget(self._side_columns["right"]),
        }
        for tab_widget in self._side_tab_widgets.values():
            tab_widget.setObjectName("dockedPanelTabs")
            tab_widget.setDocumentMode(True)
            tab_widget.setMovable(True)
        self._default_sizes = [self.library.expanded_width, 1240, self.inspector.expanded_width]
        self._wiring_complete = False

        for key, panel in self._panel_map.items():
            panel.collapse_requested.connect(self.collapse_panel)
            panel.popout_requested.connect(self.pop_out_panel)
            panel.close_requested.connect(self.hide_panel)
            panel.expand_requested.connect(self.expand_panel)
            panel.drag_popout_requested.connect(self._handle_panel_drag_popout)
            panel.drag_move_requested.connect(self._handle_floating_drag_move)
            panel.dock_drop_requested.connect(self._handle_floating_drop)
            action = QAction(f"Show {panel.title}", self.shell)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(lambda checked, target=key: self._handle_toggle_action(target, checked))
            self.toggle_actions[key] = action

        self.splitter.splitterMoved.connect(self._remember_panel_widths)
        self._wiring_complete = True
        self.reset_layout()

    def apply_theme(self, theme: ThemePalette) -> None:
        for panel in self._panel_map.values():
            panel.apply_theme(theme)

    def reset_layout(self) -> None:
        for key in tuple(self._floating_windows):
            self._dock_floating_panel(key, show_after=True)
        self.library.set_side("left")
        self.inspector.set_side("right")
        self._side_orders = {"left": ["library"], "right": ["inspector"]}
        self._side_tab_groups = {"left": [], "right": []}
        self._sync_splitter_order()
        self.library.show_expanded()
        self.inspector.show_expanded()
        self._set_action_checked("library", True)
        self._set_action_checked("inspector", True)
        self.splitter.setSizes(self._default_sizes)

    def save_state(self) -> dict[str, Any]:
        panels_state: dict[str, Any] = {}
        for key, panel in self._panel_map.items():
            window = self._floating_windows.get(key)
            panels_state[key] = {
                "mode": panel.mode,
                "side": panel.side,
                "expanded_width": panel.expanded_width,
                "floating_geometry": _encode_qbytearray(window.saveGeometry()) if window is not None else "",
            }
        return {
            "version": 3,
            "splitter_state": _encode_qbytearray(self.splitter.saveState()),
            "panels": panels_state,
            "side_orders": {
                side: [key for key in order if key in self._panel_map]
                for side, order in self._side_orders.items()
                if side in {"left", "right"}
            },
            "side_tab_groups": {
                side: [key for key in keys if key in self._panel_map]
                for side, keys in self._side_tab_groups.items()
                if side in {"left", "right"}
            },
        }

    def restore_state(self, payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        panels_state = payload.get("panels")
        if not isinstance(panels_state, dict):
            return False

        self.reset_layout()

        for key, panel_state in panels_state.items():
            panel = self._panel_map.get(key)
            if panel is None or not isinstance(panel_state, dict):
                continue
            side = panel_state.get("side")
            if side in {"left", "right"}:
                panel.set_side(side)
        self._side_orders = self._validated_side_map(payload.get("side_orders"), default=self._side_orders)
        self._side_tab_groups = self._validated_side_map(payload.get("side_tab_groups"), default={"left": [], "right": []})
        self._sync_splitter_order()

        for key, panel_state in panels_state.items():
            panel = self._panel_map.get(key)
            if panel is None or not isinstance(panel_state, dict):
                continue
            panel.set_expanded_width(int(panel_state.get("expanded_width", panel.expanded_width)))
            mode = panel_state.get("mode", "expanded")
            if mode == "hidden":
                self.hide_panel(key)
            elif mode == "collapsed":
                self.collapse_panel(key)
            elif mode == "floating":
                self.pop_out_panel(key, geometry=panel_state.get("floating_geometry", ""))
            else:
                self.expand_panel(key)

        splitter_state = _decode_qbytearray(payload.get("splitter_state"))
        if not splitter_state.isEmpty():
            self.splitter.restoreState(splitter_state)
        else:
            self._rebalance_sizes()
        return True

    def _validated_side_map(self, payload: object, *, default: dict[str, list[str]]) -> dict[str, list[str]]:
        result = {
            "left": list(default.get("left", [])),
            "right": list(default.get("right", [])),
        }
        if not isinstance(payload, dict):
            return result
        for side in ("left", "right"):
            keys = payload.get(side)
            if not isinstance(keys, list):
                continue
            result[side] = [key for key in keys if isinstance(key, str) and key in self._panel_map]
        return result

    def expand_panel(self, key: str) -> None:
        panel = self._panel_map[key]
        if key in self._floating_windows:
            self.dock_to_side(key, panel.side, show_after=True)
        panel.show_expanded()
        self._set_action_checked(key, True)
        self._rebalance_sizes()

    def collapse_panel(self, key: str) -> None:
        if key in self._floating_windows:
            panel = self._panel_map[key]
            window = self._floating_windows[key]
            if window.card_count() > 1:
                return
            if panel.viewport.isVisible():
                panel.viewport.hide()
                collapsed_height = max(64, panel.header.sizeHint().height() + 10)
                window.setMinimumHeight(collapsed_height)
                window.resize(window.width(), collapsed_height)
            else:
                panel.viewport.show()
                window.setMinimumHeight(220)
                if window.height() < 360:
                    window.resize(window.width(), 720)
            return
        panel = self._panel_map[key]
        if panel.mode == "expanded":
            panel.set_expanded_width(panel.width())
        panel.show_collapsed()
        self._set_action_checked(key, True)
        self._rebalance_sizes()

    def hide_panel(self, key: str) -> None:
        if key in self._floating_windows:
            self._dock_floating_panel(key, show_after=False)
            self._set_action_checked(key, False)
            return
        panel = self._panel_map[key]
        if panel.mode == "expanded":
            panel.set_expanded_width(panel.width())
        panel.hide_panel()
        self._set_action_checked(key, False)
        self._rebalance_sizes()

    def pop_out_panel(
        self,
        key: str,
        geometry: str | None = None,
        *,
        drag_global_pos: QPoint | None = None,
        header_drag_offset: QPoint | None = None,
    ) -> FloatingPanelWindow | None:
        if key in self._floating_windows:
            if drag_global_pos is None:
                self.dock_to_side(key, self._panel_map[key].side, show_after=True)
            return self._floating_windows.get(key)

        panel = self._panel_map[key]
        if panel.mode == "expanded":
            panel.set_expanded_width(panel.width())

        start_pos = panel.mapToGlobal(QPoint(12, 32))
        window = FloatingPanelWindow(panel.title, parent=self.shell.window())
        window.close_requested.connect(lambda target=window: self._handle_floating_close_request(target))
        window.maximized_changed.connect(lambda maximized, target=key: self._handle_floating_maximize_request(target, maximized))
        window.set_card(key, panel.title, panel.detach_frame())
        self._floating_windows[key] = window

        panel.set_floating()
        panel.set_floating_state(True)
        self._set_action_checked(key, True)
        self._rebalance_sizes()

        if geometry:
            floating_geometry = _decode_qbytearray(geometry)
            if not floating_geometry.isEmpty():
                window.restoreGeometry(floating_geometry)
        else:
            window.move(self._clamped_palette_position(start_pos, window.size()))
        window.show()
        if drag_global_pos is not None and header_drag_offset is not None:
            header_global = panel.header.mapToGlobal(header_drag_offset)
            target_pos = window.pos() + (drag_global_pos - header_global)
            window.move(self._clamped_palette_position(target_pos, window.size()))
            panel.header.start_floating_drag(drag_global_pos)
        window.raise_()
        window.activateWindow()
        return window

    def dock_to_side(self, key: str, side: str, *, show_after: bool = True) -> None:
        panel = self._panel_map[key]
        if side not in {"left", "right"}:
            side = panel.side
        panel.set_side(side)
        self._move_side_order(key, side)
        self._remove_from_tab_groups(key)
        self._sync_splitter_order()

        if key in self._floating_windows:
            self._dock_floating_panel(key, show_after=show_after)
            return

        if show_after:
            panel.show_expanded()
            self._set_action_checked(key, True)
        else:
            panel.hide_panel()
            self._set_action_checked(key, False)
        self._rebalance_sizes()

    def swap_sides(self) -> None:
        self.library.set_side("right" if self.library.side == "left" else "left")
        self.inspector.set_side("right" if self.inspector.side == "left" else "left")
        self._side_orders = {
            "left": [key for key, panel in self._panel_map.items() if panel.side == "left"],
            "right": [key for key, panel in self._panel_map.items() if panel.side == "right"],
        }
        self._side_tab_groups = {"left": [], "right": []}
        self._sync_splitter_order()
        self._rebalance_sizes()

    def _dock_floating_panel(self, key: str, *, show_after: bool) -> None:
        panel = self._panel_map[key]
        window = self._floating_windows.get(key)
        frame = None
        if window is not None:
            frame = window.take_card(key)
            self._floating_windows.pop(key, None)
            if window.card_count() == 0:
                window.hide()
                window.deleteLater()
        panel.viewport.show()
        panel.attach_frame(frame)
        if show_after:
            panel.show_expanded()
            self._set_action_checked(key, True)
        else:
            panel.hide_panel()
            self._set_action_checked(key, False)
        panel.set_floating_state(False)
        self._move_side_order(key, panel.side)
        self._remove_from_tab_groups(key)
        self._sync_splitter_order()
        self._rebalance_sizes()

    def _handle_floating_close_request(self, window: FloatingPanelWindow) -> None:
        for key in window.keys():
            self._dock_floating_panel(key, show_after=False)

    def _handle_panel_drag_popout(self, key: str, global_pos: QPoint, header_offset: QPoint) -> None:
        self.pop_out_panel(key, drag_global_pos=global_pos, header_drag_offset=header_offset)
        self._show_snap_preview_for_drag(key, global_pos)

    def _handle_floating_drag_move(self, key: str, global_pos: QPoint) -> None:
        self._show_snap_preview_for_drag(key, global_pos)

    def _handle_floating_maximize_request(self, key: str, maximized: bool) -> None:
        if not maximized or key not in self._floating_windows:
            return
        QTimer.singleShot(0, lambda target=key: self._dock_floating_to_stored_side(target))

    def _handle_floating_drop(self, key: str, global_pos) -> None:
        self._hide_snap_preview()
        if self._snap_floating_panel(key, global_pos):
            return
        dock_side = self._resolve_drop_side(global_pos)
        if dock_side is None:
            return
        self.dock_to_side(key, dock_side, show_after=True)

    def _show_snap_preview_for_drag(self, key: str, global_pos: QPoint) -> None:
        self._snap_preview.show_global_rect(self._snap_preview_rect_for_drag(key, global_pos))

    def _hide_snap_preview(self) -> None:
        self._snap_preview.hide()

    def _dock_floating_to_stored_side(self, key: str) -> None:
        if key not in self._floating_windows:
            return
        self.dock_to_side(key, self._panel_map[key].side, show_after=True)

    def _handle_toggle_action(self, key: str, checked: bool) -> None:
        if not self._wiring_complete:
            return
        panel = self._panel_map[key]
        if checked:
            if panel.mode == "floating":
                return
            self.expand_panel(key)
            return
        self.hide_panel(key)

    def _remember_panel_widths(self) -> None:
        sizes = self.splitter.sizes()
        if len(sizes) != 3:
            return
        for panel in self._panels_for_side("left"):
            if panel.mode == "expanded" and sizes[0] > TAB_WIDTH:
                panel.set_expanded_width(sizes[0])
        for panel in self._panels_for_side("right"):
            if panel.mode == "expanded" and sizes[2] > TAB_WIDTH:
                panel.set_expanded_width(sizes[2])

    def _rebalance_sizes(self) -> None:
        # Refresh each side column's width bounds and visibility for the current
        # panel modes so a hidden/collapsed panel never keeps reserving space —
        # the center viewport reclaims it instead of leaving dead space.
        for side in ("left", "right"):
            column = self._side_columns[side]
            docked = [panel for panel in self._panels_for_side(side) if panel.mode in {"expanded", "collapsed"}]
            self._apply_column_width_constraints(column, docked)
            column.setVisible(bool(docked))
        total = max(sum(self.splitter.sizes()), self.splitter.width(), 1200)
        left = self._side_width("left")
        right = self._side_width("right")
        center = max(720, total - left - right)
        self.splitter.setSizes([left, center, right])

    def _side_width(self, side: str) -> int:
        widths: list[int] = []
        for panel in self._panels_for_side(side):
            if panel.mode == "expanded":
                widths.append(panel.expanded_width)
            elif panel.mode == "collapsed":
                widths.append(TAB_WIDTH)
        return max(widths) if widths else 0

    def _panels_for_side(self, side: str) -> list[WorkspacePanel]:
        panels: list[WorkspacePanel] = []
        seen: set[str] = set()
        for key in self._side_orders.get(side, []):
            panel = self._panel_map.get(key)
            if panel is not None and panel.side == side:
                panels.append(panel)
                seen.add(key)
        for key, panel in self._panel_map.items():
            if key not in seen and panel.side == side:
                panels.append(panel)
        return panels

    def _sync_splitter_order(self) -> None:
        self._sync_side_column("left")
        self._sync_side_column("right")
        self.splitter.insertWidget(0, self._side_columns["left"])
        if self.center is not None:
            self.splitter.insertWidget(1, self.center)
        self.splitter.insertWidget(2, self._side_columns["right"])
        self._side_columns["left"].show()
        self._side_columns["right"].show()
        self.splitter.updateGeometry()
        self.splitter.update()

    def _sync_side_column(self, side: str) -> None:
        column = self._side_columns[side]
        self._clear_splitter(column)
        panels = [panel for panel in self._panels_for_side(side) if panel.mode != "floating"]
        visible_panels = [panel for panel in panels if panel.mode != "hidden"]
        tab_keys = [key for key in self._side_tab_groups.get(side, []) if self._panel_map.get(key) in visible_panels]
        tab_widget = self._side_tab_widgets[side]
        self._clear_tabs(tab_widget)

        tabbed = set(tab_keys) if len(tab_keys) > 1 else set()
        for panel in visible_panels:
            key = panel.key
            if key in tabbed:
                if tab_widget.parent() is not column:
                    tab_widget.setParent(column)
                if column.indexOf(tab_widget) < 0:
                    column.addWidget(tab_widget)
                tab_widget.addTab(panel, panel.title)
                panel.show()
                if key == tab_keys[-1]:
                    tab_widget.setCurrentWidget(panel)
                continue
            column.addWidget(panel)
            panel.show()
        tab_widget.tabBar().setVisible(len(tabbed) > 1)
        tab_widget.setVisible(len(tabbed) > 0)
        column.setVisible(bool(visible_panels))
        self._apply_column_width_constraints(column, visible_panels)
        if column.count() > 1:
            column.setSizes([1 for _ in range(column.count())])
        column.updateGeometry()
        column.update()

    @staticmethod
    def _apply_column_width_constraints(column: QSplitter, panels: list["WorkspacePanel"]) -> None:
        # Mirror the contained panels' width bounds onto the side column so the
        # main splitter handle cannot drag a docked panel narrower (clipping its
        # contents) or wider than its allowed range.
        expanded = [panel for panel in panels if panel.mode == "expanded"]
        if expanded:
            minimum = max(panel.minimum_expanded_width for panel in expanded)
            maximum = max(panel.maximum_expanded_width for panel in expanded)
        elif any(panel.mode == "collapsed" for panel in panels):
            minimum = maximum = TAB_WIDTH
        else:
            minimum, maximum = 0, 16777215
        column.setMinimumWidth(minimum)
        column.setMaximumWidth(max(minimum, maximum))

    @staticmethod
    def _clear_splitter(splitter: QSplitter) -> None:
        while splitter.count():
            widget = splitter.widget(0)
            if widget is None:
                break
            widget.setParent(None)

    @staticmethod
    def _clear_tabs(tab_widget: QTabWidget) -> None:
        while tab_widget.count():
            widget = tab_widget.widget(0)
            tab_widget.removeTab(0)
            if widget is not None:
                widget.setParent(None)

    def _move_side_order(self, key: str, side: str, *, target_key: str | None = None, placement: str = "bottom") -> None:
        for order in self._side_orders.values():
            if key in order:
                order.remove(key)
        order = self._side_orders.setdefault(side, [])
        if target_key in order:
            target_index = order.index(target_key)
            insert_index = target_index if placement in {"top", "left"} else target_index + 1
            order.insert(insert_index, key)
        else:
            order.append(key)

    def _remove_from_tab_groups(self, key: str) -> None:
        for side, keys in self._side_tab_groups.items():
            if key in keys:
                self._side_tab_groups[side] = [item for item in keys if item != key]

    def _resolve_drop_side(self, global_pos) -> str | None:
        if self.shell is None:
            return None
        shell_top_left = self.shell.mapToGlobal(QPoint(0, 0))
        shell_rect = QRect(shell_top_left, self.shell.size())
        if not shell_rect.contains(global_pos):
            return None
        zone_width = max(88, min(180, shell_rect.width() // 6))
        left_zone = QRect(shell_rect.left(), shell_rect.top(), zone_width, shell_rect.height())
        right_zone = QRect(shell_rect.right() - zone_width + 1, shell_rect.top(), zone_width, shell_rect.height())
        if left_zone.contains(global_pos):
            return "left"
        if right_zone.contains(global_pos):
            return "right"
        return None

    def _snap_preview_rect_for_drag(self, key: str, global_pos: QPoint) -> QRect | None:
        window = self._floating_windows.get(key)
        if window is None:
            return None
        dragged_rect = window.frameGeometry()
        for other_window in self._unique_floating_windows(excluding=window):
            other_rect = other_window.frameGeometry()
            operation = self._snap_operation_for_window(global_pos, dragged_rect, other_window)
            if operation is not None:
                action, placement, _target_key = operation
                return self._preview_rect_for_operation(dragged_rect, other_rect, action, placement)
            snapped_pos = self._snap_position_for_rects(dragged_rect, other_rect)
            if snapped_pos is not None:
                return QRect(snapped_pos, dragged_rect.size())

        for target_key, target_panel in self._panel_map.items():
            if target_key == key or target_key in self._floating_windows or target_panel.mode not in {"expanded", "collapsed"}:
                continue
            target_rect = QRect(target_panel.frame.mapToGlobal(QPoint(0, 0)), target_panel.frame.size())
            operation = self._snap_operation_for_rect(global_pos, dragged_rect, target_rect, target_key)
            if operation is not None:
                action, placement, _target_key = operation
                return self._preview_rect_for_operation(dragged_rect, target_rect, action, placement)

        dock_side = self._resolve_drop_side(global_pos)
        if dock_side is not None:
            return self._dock_side_preview_rect(dock_side)
        return None

    def _preview_rect_for_operation(self, dragged_rect: QRect, target_rect: QRect, action: str, placement: str) -> QRect | None:
        if action == "stack":
            height = min(72, max(44, target_rect.height() // 6))
            return QRect(target_rect.left(), target_rect.top(), target_rect.width(), height)
        if action == "split":
            if placement == "top":
                return QRect(target_rect.left(), target_rect.top(), target_rect.width(), max(96, target_rect.height() // 2))
            if placement == "bottom":
                height = max(96, target_rect.height() // 2)
                return QRect(target_rect.left(), target_rect.bottom() - height + 1, target_rect.width(), height)
        if action == "move":
            snapped_pos = self._snap_position_for_rects(dragged_rect, target_rect)
            if snapped_pos is not None:
                return QRect(snapped_pos, dragged_rect.size())
        return None

    def _dock_side_preview_rect(self, side: str) -> QRect | None:
        if self.shell is None:
            return None
        shell_top_left = self.shell.mapToGlobal(QPoint(0, 0))
        shell_rect = QRect(shell_top_left, self.shell.size())
        zone_width = max(88, min(180, shell_rect.width() // 6))
        if side == "left":
            return QRect(shell_rect.left(), shell_rect.top(), zone_width, shell_rect.height())
        if side == "right":
            return QRect(shell_rect.right() - zone_width + 1, shell_rect.top(), zone_width, shell_rect.height())
        return None

    def _snap_floating_panel(self, key: str, global_pos: QPoint) -> bool:
        window = self._floating_windows.get(key)
        if window is None:
            return False
        dragged_rect = window.frameGeometry()
        for other_window in self._unique_floating_windows(excluding=window):
            other_rect = other_window.frameGeometry()
            operation = self._snap_operation_for_window(global_pos, dragged_rect, other_window)
            if operation is not None:
                action, placement, target_key = operation
                if action == "stack":
                    self._stack_floating_panel(key, other_window, target_key=target_key)
                elif action == "split":
                    self._split_floating_panel(key, other_window, placement=placement, target_key=target_key)
                else:
                    snapped_pos = self._snap_position_for_rects(dragged_rect, other_rect)
                    if snapped_pos is not None:
                        window.move(snapped_pos)
                return True
            snapped_pos = self._snap_position_for_rects(dragged_rect, other_rect)
            if snapped_pos is not None:
                window.move(snapped_pos)
                return True
        for target_key, target_panel in self._panel_map.items():
            if target_key == key or target_key in self._floating_windows or target_panel.mode not in {"expanded", "collapsed"}:
                continue
            target_rect = QRect(target_panel.frame.mapToGlobal(QPoint(0, 0)), target_panel.frame.size())
            operation = self._snap_operation_for_rect(global_pos, dragged_rect, target_rect, target_key)
            if operation is None:
                continue
            action, placement, snapped_target_key = operation
            if action == "stack":
                self._dock_floating_panel_to_docked_target(
                    key,
                    target_key,
                    placement=placement,
                    target_key_for_order=snapped_target_key,
                    tabbed=True,
                )
            elif action == "split":
                self._dock_floating_panel_to_docked_target(
                    key,
                    target_key,
                    placement=placement,
                    target_key_for_order=snapped_target_key,
                    tabbed=False,
                )
            else:
                snapped_pos = self._snap_position_for_rects(dragged_rect, target_rect)
                if snapped_pos is not None:
                    window.move(snapped_pos)
            return True
        return False

    def _snap_operation_for_window(
        self,
        global_pos: QPoint,
        dragged_rect: QRect,
        target_window: FloatingPanelWindow,
    ) -> tuple[str, str, str | None] | None:
        target_rect = target_window.frameGeometry()
        target_key = target_window.key_at_global_pos(global_pos)
        if target_key is None:
            keys = target_window.keys()
            target_key = keys[0] if keys else None
        return self._snap_operation_for_rect(global_pos, dragged_rect, target_rect, target_key)

    def _snap_operation_for_rect(
        self,
        global_pos: QPoint,
        dragged_rect: QRect,
        target_rect: QRect,
        target_key: str | None,
    ) -> tuple[str, str, str | None] | None:
        distance = self._snap_distance
        if not target_rect.adjusted(-distance, -distance, distance, distance).contains(global_pos):
            return None
        tab_zone_height = min(74, max(46, target_rect.height() // 6))
        tab_zone = QRect(target_rect.left(), target_rect.top(), target_rect.width(), tab_zone_height)
        if tab_zone.adjusted(0, -distance, 0, distance // 2).contains(global_pos):
            return ("stack", "tabs", target_key)

        horizontal_overlap = min(dragged_rect.right(), target_rect.right()) - max(dragged_rect.left(), target_rect.left())
        vertical_overlap = min(dragged_rect.bottom(), target_rect.bottom()) - max(dragged_rect.top(), target_rect.top())
        if horizontal_overlap > 64:
            if abs(dragged_rect.top() - target_rect.bottom()) <= distance or global_pos.y() >= target_rect.center().y():
                return ("split", "bottom", target_key)
            if abs(dragged_rect.bottom() - target_rect.top()) <= distance or global_pos.y() > target_rect.top() + tab_zone_height:
                return ("split", "top", target_key)
        if vertical_overlap > 64:
            if abs(dragged_rect.left() - target_rect.right()) <= distance:
                return ("move", "right", target_key)
            if abs(dragged_rect.right() - target_rect.left()) <= distance:
                return ("move", "left", target_key)
        return None

    def _dock_floating_panel_to_docked_target(
        self,
        key: str,
        docked_target_key: str,
        *,
        placement: str,
        target_key_for_order: str | None,
        tabbed: bool,
    ) -> None:
        source_window = self._floating_windows.get(key)
        target_panel = self._panel_map.get(docked_target_key)
        panel = self._panel_map.get(key)
        if source_window is None or target_panel is None or panel is None:
            return
        frame = source_window.take_card(key)
        self._floating_windows.pop(key, None)
        if source_window.card_count() == 0:
            source_window.hide()
            source_window.deleteLater()
        if frame is None:
            return

        side = target_panel.side
        panel.attach_frame(frame)
        panel.viewport.show()
        panel.set_side(side)
        panel.set_floating_state(False)
        panel.show_expanded()
        self._set_action_checked(key, True)
        self._move_side_order(key, side, target_key=target_key_for_order or docked_target_key, placement=placement)
        if tabbed:
            group = list(self._side_tab_groups.get(side, []))
            if docked_target_key not in group:
                group = [docked_target_key]
            if key in group:
                group.remove(key)
            group.append(key)
            self._side_tab_groups[side] = group
        else:
            self._remove_from_tab_groups(key)
            self._remove_from_tab_groups(docked_target_key)
        self._sync_splitter_order()
        self._rebalance_sizes()

    def _unique_floating_windows(self, *, excluding: FloatingPanelWindow | None = None) -> tuple[FloatingPanelWindow, ...]:
        windows: list[FloatingPanelWindow] = []
        for window in self._floating_windows.values():
            if window is excluding or window in windows:
                continue
            windows.append(window)
        return tuple(windows)

    def _stack_floating_panel(self, key: str, target_window: FloatingPanelWindow, *, target_key: str | None = None) -> None:
        source_window = self._floating_windows.get(key)
        if source_window is None or source_window is target_window:
            return
        panel = self._panel_map[key]
        frame = source_window.take_card(key)
        self._floating_windows.pop(key, None)
        if source_window.card_count() == 0:
            source_window.hide()
            source_window.deleteLater()
        if frame is None:
            return
        panel.viewport.show()
        target_window.set_card(key, panel.title, frame, target_key=target_key)
        self._floating_windows[key] = target_window
        target_window.raise_()
        target_window.activateWindow()

    def _split_floating_panel(
        self,
        key: str,
        target_window: FloatingPanelWindow,
        *,
        placement: str,
        target_key: str | None = None,
    ) -> None:
        source_window = self._floating_windows.get(key)
        if source_window is None or source_window is target_window:
            return
        panel = self._panel_map[key]
        source_rect = source_window.frameGeometry()
        target_rect = target_window.frameGeometry()
        frame = source_window.take_card(key)
        self._floating_windows.pop(key, None)
        if source_window.card_count() == 0:
            source_window.hide()
            source_window.deleteLater()
        if frame is None:
            return

        panel.viewport.show()
        target_window.split_card(key, panel.title, frame, target_key=target_key, placement=placement)
        self._floating_windows[key] = target_window
        if placement in {"top", "bottom"}:
            target_window.setGeometry(self._shared_column_geometry(source_rect, target_rect, placement))
        target_window.raise_()
        target_window.activateWindow()

    def _shared_column_geometry(self, source: QRect, target: QRect, placement: str) -> QRect:
        width = max(source.width(), target.width(), 300)
        if placement == "top":
            top = min(source.top(), target.top())
            bottom = max(target.bottom(), source.bottom())
        else:
            top = min(target.top(), source.top())
            bottom = max(source.bottom(), target.bottom())
        height = max(bottom - top + 1, min(900, target.height() + max(220, source.height() // 2)))
        left = target.left()
        return QRect(left, top, width, height)

    def _snap_position_for_rects(self, dragged: QRect, target: QRect) -> QPoint | None:
        distance = self._snap_distance
        vertical_overlap = min(dragged.bottom(), target.bottom()) - max(dragged.top(), target.top())
        horizontal_overlap = min(dragged.right(), target.right()) - max(dragged.left(), target.left())
        if vertical_overlap > 48:
            if abs(dragged.left() - target.right()) <= distance:
                return QPoint(target.right() + 1, target.top())
            if abs(dragged.right() - target.left()) <= distance:
                return QPoint(target.left() - dragged.width() - 1, target.top())
            if abs(dragged.left() - target.left()) <= distance:
                return QPoint(target.left(), dragged.top())
            if abs(dragged.right() - target.right()) <= distance:
                return QPoint(target.right() - dragged.width() + 1, dragged.top())
        if horizontal_overlap > 48:
            if abs(dragged.top() - target.bottom()) <= distance:
                return QPoint(target.left(), target.bottom() + 1)
            if abs(dragged.bottom() - target.top()) <= distance:
                return QPoint(target.left(), target.top() - dragged.height() - 1)
            if abs(dragged.top() - target.top()) <= distance:
                return QPoint(dragged.left(), target.top())
            if abs(dragged.bottom() - target.bottom()) <= distance:
                return QPoint(dragged.left(), target.bottom() - dragged.height() + 1)
        return None

    def _clamped_palette_position(self, global_pos: QPoint, size: QSize) -> QPoint:
        if self.shell is None:
            return global_pos
        shell_top_left = self.shell.mapToGlobal(QPoint(0, 0))
        shell_rect = QRect(shell_top_left, self.shell.size()).adjusted(12, 12, -12, -12)
        x = min(max(global_pos.x(), shell_rect.left()), max(shell_rect.left(), shell_rect.right() - size.width()))
        y = min(max(global_pos.y(), shell_rect.top()), max(shell_rect.top(), shell_rect.bottom() - size.height()))
        return QPoint(x, y)

    def _set_action_checked(self, key: str, checked: bool) -> None:
        action = self.toggle_actions[key]
        if action.isChecked() == checked:
            return
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)


class InspectorPanel(QWidget):
    keep_requested = Signal()
    reject_requested = Signal()
    compare_requested = Signal()
    best_of_set_requested = Signal()
    open_editor_requested = Signal()
    reveal_requested = Signal()
    popout_requested = Signal()
    swap_side_requested = Signal()
    close_requested = Signal()
    preview_requested = Signal()
    face_cycle_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("inspectorPanelContent")

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setObjectName("inspectorScrollArea")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        # The real scrollbar is hidden (takes no layout width) so the cards stay
        # flush with the panel's right edge; an overlay scrollbar floats over the
        # content when scrolling is actually needed.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root_layout.addWidget(scroll)
        self._scroll = scroll
        self._overlay_scrollbar = QScrollBar(Qt.Orientation.Vertical, scroll)
        self._overlay_scrollbar.setObjectName("inspectorOverlayScrollBar")
        self._overlay_scrollbar.hide()
        real_bar = scroll.verticalScrollBar()
        self._overlay_scrollbar.valueChanged.connect(real_bar.setValue)
        real_bar.valueChanged.connect(self._overlay_scrollbar.setValue)
        real_bar.rangeChanged.connect(lambda _lo, _hi: self._sync_overlay_scrollbar())

        content = QWidget(scroll)
        content.setObjectName("inspectorBody")
        layout = QVBoxLayout(content)
        # Flush on all sides so the cards' right edge stays colinear with the top
        # bar; a vertical scrollbar (when present) takes its own space rather than
        # overlapping the cards.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._inspector_body_layout = layout

        # Preview card: the small header bar (pop-out / swap / close) sits above
        # the selection preview image, replacing the old folder-name header.
        self.preview_card = QWidget(content)
        self.preview_card.setObjectName("inspectorPreviewCard")
        self.preview_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.preview_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        preview_layout = QVBoxLayout(self.preview_card)
        preview_layout.setContentsMargins(8, 8, 8, 10)
        preview_layout.setSpacing(8)

        header_bar = QWidget(self.preview_card)
        header_bar.setObjectName("inspectorHeaderBar")
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self.preview_collapse_button = self._make_header_button("▾", "Minimize preview", "inspectorHeaderButton")
        self.preview_collapse_button.setCheckable(True)
        self.preview_collapse_button.setChecked(True)
        self.preview_collapse_button.toggled.connect(self._set_preview_expanded)
        self.popout_button = self._make_header_button("⊞", "Pop out inspector", "inspectorHeaderButton")
        self.swap_side_button = self._make_header_button("⌖", "Swap the left and right panel sides", "inspectorHeaderButton")
        self.face_cycle_button = self._make_header_button("↻", "Next detected face", "inspectorHeaderButton")
        self.close_button = self._make_header_button("✕", "Hide inspector", "inspectorHeaderCloseButton")
        self.popout_button.clicked.connect(lambda _checked=False: self.popout_requested.emit())
        self.swap_side_button.clicked.connect(lambda _checked=False: self.swap_side_requested.emit())
        self.face_cycle_button.clicked.connect(lambda _checked=False: self.face_cycle_requested.emit())
        self.close_button.clicked.connect(lambda _checked=False: self.close_requested.emit())
        header_layout.addWidget(self.preview_collapse_button, 0)
        header_layout.addWidget(self.popout_button, 0)
        header_layout.addWidget(self.swap_side_button, 0)
        header_layout.addWidget(self.face_cycle_button, 0)
        header_layout.addStretch(1)
        header_layout.addWidget(self.close_button, 0)
        preview_layout.addWidget(header_bar)

        self.preview_image = QLabel(self.preview_card)
        self.preview_image.setObjectName("inspectorPreviewImage")
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setMinimumHeight(0)
        # Ignored width + zero minimum so a loaded pixmap never forces the panel
        # wider than its column (which would clip everything on the right when
        # the panel is resized narrow). The pixmap is rescaled on resize instead.
        self.preview_image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.preview_image.setMinimumWidth(0)
        self.preview_image.setText("No image selected")
        self.preview_image.setMouseTracking(True)
        self.preview_image.installEventFilter(self)
        self._preview_source: QImage | None = None
        self._preview_zoom = 1.0
        self._preview_focus = (0.5, 0.5)
        preview_layout.addWidget(self.preview_image, 1)

        layout.addWidget(self.preview_card)

        # Histogram sits directly below the preview.
        self.histogram_widget = InspectorHistogram(self)
        self._make_custom_section(layout, "Histogram", self.histogram_widget)
        self.culling_rows = self._make_section(layout, "Culling", ("Decision", "Rating", "AI Suggestion", "Confidence", "Reason"))
        self.subject_rows = self._make_section(layout, "Subject", ("Type", "Review Focus", "Signal", "AI Detail"))
        self.quality_rows = self._make_section(layout, "Quality", ("Detail", "Focus", "Motion Blur", "Noise", "Exposure", "Confidence"))
        self.group_rows = self._make_section(
            layout,
            "Group Comparison",
            ("Group Size", "Rank", "Best Candidate", "Similar Files", "Duplicate Risk", "Why"),
        )
        self.edit_rows = self._make_section(layout, "Edit Potential", ("Worth Editing", "Main Issue", "Fixes Needed", "Effort", "Notes"))
        # Quick Actions now live in the left "Review Controls" pane.
        self.quick_action_buttons: dict[str, QPushButton] = {}
        scroll.setWidget(content)
        QTimer.singleShot(0, self._sync_preview_card_aspect)

        self.clear()

    def _make_custom_section(self, layout: QVBoxLayout, title: str, widget: QWidget) -> QWidget:
        section = QWidget(self)
        section.setObjectName("inspectorSection")
        section.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(10, 7, 10, 7)
        section_layout.setSpacing(5)
        body = QWidget(section)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(widget)
        self._add_section_header(section_layout, title, body)
        section_layout.addWidget(body)
        layout.addWidget(section)
        return section

    def _add_section_header(self, layout: QVBoxLayout, title: str, body: QWidget, *, collapsed: bool = False) -> None:
        header = QWidget(self)
        header.setObjectName("inspectorSectionHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setObjectName("inspectorSectionTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(title_label, 1)

        toggle = QToolButton(header)
        toggle.setObjectName("inspectorSectionToggle")
        toggle.setCheckable(True)
        toggle.setAutoRaise(True)

        def update_section(visible: bool, *, target: QWidget = body, button: QToolButton = toggle, label: str = title) -> None:
            target.setVisible(visible)
            button.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)
            button.setToolTip(f"{'Collapse' if visible else 'Expand'} {label}")

        toggle.toggled.connect(update_section)
        toggle.setChecked(not collapsed)
        update_section(not collapsed)
        header_layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(header)

    def _make_section(
        self,
        layout: QVBoxLayout,
        title: str,
        rows: tuple[str, ...],
        *,
        section: QWidget | None = None,
        collapsed: bool = False,
        stretch: int = 0,
    ) -> dict[str, QLabel]:
        target = section or QWidget(self)
        target.setObjectName("inspectorSection")
        target.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        section_layout = QVBoxLayout(target)
        section_layout.setContentsMargins(10, 7, 10, 7)
        section_layout.setSpacing(5)

        body = QWidget(target)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        values: dict[str, QLabel] = {}
        for row_index, row_name in enumerate(rows):
            label = QLabel(row_name)
            label.setObjectName("inspectorKey")
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            value = QLabel("")
            value.setObjectName("inspectorValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, row_index, 0)
            grid.addWidget(value, row_index, 1)
            values[row_name] = value
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        body.setLayout(grid)
        if title:
            self._add_section_header(section_layout, title, body, collapsed=collapsed)
        section_layout.addWidget(body)
        if stretch:
            # Keep the rows pinned to the top while the section card itself
            # stretches to fill the remaining height.
            section_layout.addStretch(1)
        layout.addWidget(target, stretch)
        return values

    def _make_quick_actions(self, layout: QVBoxLayout) -> dict[str, QPushButton]:
        section = QWidget(self)
        section.setObjectName("inspectorSection")
        section.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(10, 7, 10, 7)
        section_layout.setSpacing(5)

        body = QWidget(section)
        row = QGridLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setHorizontalSpacing(6)
        row.setVerticalSpacing(6)
        buttons = {
            "keep": self._quick_button("Keep", self.keep_requested),
            "reject": self._quick_button("Reject", self.reject_requested),
            "editor": self._quick_button("Open in Editor", self.open_editor_requested),
            "reveal": self._quick_button("Reveal File", self.reveal_requested),
        }
        for index, button in enumerate(buttons.values()):
            row.addWidget(button, index // 2, index % 2)
        body.setLayout(row)
        self._add_section_header(section_layout, "Quick Actions", body)
        section_layout.addWidget(body)
        layout.addWidget(section)
        return buttons

    def _quick_button(self, text: str, signal: Signal | None) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("inspectorActionButton")
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if signal is not None:
            button.clicked.connect(lambda _checked=False, target=signal: target.emit())
        return button

    def _make_header_button(self, text: str, tooltip: str, object_name: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName(object_name)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedSize(24, 22)
        return button

    def _set_preview_expanded(self, expanded: bool) -> None:
        self.preview_image.setVisible(expanded)
        self.preview_collapse_button.setText("▾" if expanded else "▸")
        self.preview_collapse_button.setToolTip("Minimize preview" if expanded else "Expand preview")
        self._sync_preview_card_aspect()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.preview_image:
            event_type = event.type()
            if event_type == QEvent.Type.Wheel:
                source = getattr(self, "_preview_source", None)
                if source is None or source.isNull():
                    return False
                self._update_preview_focus_from_event(event)
                delta = event.angleDelta().y()
                if delta:
                    factor = 1.18 if delta > 0 else 1 / 1.18
                    self._preview_zoom = max(1.0, min(5.0, self._preview_zoom * factor))
                    self._rescale_preview()
                    return True
            if event_type == QEvent.Type.MouseMove and getattr(self, "_preview_zoom", 1.0) > 1.0:
                self._update_preview_focus_from_event(event)
                self._rescale_preview()
        return super().eventFilter(watched, event)

    def _update_preview_focus_from_event(self, event) -> None:
        try:
            position = event.position()
            x = float(position.x())
            y = float(position.y())
        except AttributeError:
            position = event.pos()
            x = float(position.x())
            y = float(position.y())
        width = max(1.0, float(self.preview_image.width()))
        height = max(1.0, float(self.preview_image.height()))
        self._preview_focus = (
            max(0.0, min(1.0, x / width)),
            max(0.0, min(1.0, y / height)),
        )

    def _sync_preview_card_aspect(self) -> None:
        card = getattr(self, "preview_card", None)
        if card is None:
            return
        if not self.preview_image.isVisible():
            target_height = INSPECTOR_PREVIEW_COLLAPSED_HEIGHT
        else:
            target_height = max(80, int(card.width() or self.width() or 0))
        if card.height() != target_height:
            card.setFixedHeight(target_height)
        self._rescale_preview()

    def set_preview(
        self,
        image: "QImage | None",
        *,
        placeholder: str = "No image selected",
        fill: bool = True,
    ) -> None:
        self._preview_aspect_mode = (
            Qt.AspectRatioMode.KeepAspectRatioByExpanding
            if fill
            else Qt.AspectRatioMode.KeepAspectRatio
        )
        if image is None or image.isNull():
            self._preview_source = None
            self._preview_zoom = 1.0
            self._preview_focus = (0.5, 0.5)
            self.preview_image.setPixmap(QPixmap())
            self.preview_image.setText(placeholder)
            return
        self._preview_source = image
        self._preview_zoom = 1.0
        self._preview_focus = (0.5, 0.5)
        self.preview_image.setText("")
        self._rescale_preview()

    def _rescale_preview(self) -> None:
        source = getattr(self, "_preview_source", None)
        if source is None or source.isNull():
            return
        display_source = source
        zoom = float(getattr(self, "_preview_zoom", 1.0) or 1.0)
        if zoom > 1.01:
            focus_x, focus_y = getattr(self, "_preview_focus", (0.5, 0.5))
            crop_width = max(1, min(source.width(), int(round(source.width() / zoom))))
            crop_height = max(1, min(source.height(), int(round(source.height() / zoom))))
            center_x = int(round(float(focus_x) * source.width()))
            center_y = int(round(float(focus_y) * source.height()))
            left = max(0, min(center_x - crop_width // 2, source.width() - crop_width))
            top = max(0, min(center_y - crop_height // 2, source.height() - crop_height))
            display_source = source.copy(QRect(left, top, crop_width, crop_height))
        width = max(20, self.preview_image.width())
        height = max(20, self.preview_image.height())
        self.preview_image.setPixmap(
            QPixmap.fromImage(display_source).scaled(
                QSize(width, height),
                getattr(
                    self,
                    "_preview_aspect_mode",
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                ),
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _sync_overlay_scrollbar(self) -> None:
        scroll = getattr(self, "_scroll", None)
        bar = getattr(self, "_overlay_scrollbar", None)
        if scroll is None or bar is None:
            return
        real_bar = scroll.verticalScrollBar()
        bar.setRange(real_bar.minimum(), real_bar.maximum())
        bar.setPageStep(real_bar.pageStep())
        bar.setSingleStep(max(1, real_bar.singleStep()))
        bar.setValue(real_bar.value())
        bar.setVisible(real_bar.maximum() > real_bar.minimum())
        self._position_overlay_scrollbar()

    def _position_overlay_scrollbar(self) -> None:
        scroll = getattr(self, "_scroll", None)
        bar = getattr(self, "_overlay_scrollbar", None)
        if scroll is None or bar is None:
            return
        width = bar.sizeHint().width() or 8
        bar.setGeometry(scroll.width() - width, 0, width, scroll.height())
        bar.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_preview_card_aspect()
        self._sync_overlay_scrollbar()

    def clear(self) -> None:
        self.set_preview(None)
        for rows in (
            self.culling_rows,
            self.subject_rows,
            self.quality_rows,
            self.group_rows,
            self.edit_rows,
        ):
            for value in rows.values():
                value.setText("-")
        self.culling_rows["Decision"].setText("Unreviewed")
        self.culling_rows["AI Suggestion"].setText("No AI result loaded")
        self.face_cycle_button.setVisible(False)
        self.face_cycle_button.setEnabled(False)
        self.subject_rows["Type"].setText("Not analyzed")
        self.subject_rows["Review Focus"].setText("-")
        self.subject_rows["Signal"].setText("-")
        self.subject_rows["AI Detail"].setText("-")
        self.quality_rows["Detail"].setText("Not analyzed")
        self.quality_rows["Focus"].setText("Not analyzed")
        self.quality_rows["Motion Blur"].setText("Not analyzed")
        self.quality_rows["Noise"].setText("Not analyzed")
        self.quality_rows["Exposure"].setText("Not analyzed")
        self.quality_rows["Confidence"].setText("Not analyzed")
        self.group_rows["Group Size"].setText("Single image / No similar group")
        self.edit_rows["Worth Editing"].setText("Not analyzed")
        self.histogram_widget.set_stats(None)
        self._set_quick_actions_enabled(False, grouped=False)

    def set_context(
        self,
        *,
        folder: str,
        mode_label: str,
        selected_count: int,
        current_record: "ImageRecord | None",
        display_path: str,
        annotation: "SessionAnnotation | None",
        ai_result: "AIImageResult | None",
        metadata: "CaptureMetadata | None" = None,
        inspection_stats: InspectionStats | None = None,
        review_insight: object | None = None,
        workflow_insight: object | None = None,
        review_summary: str = "",
        workflow_summary: str = "",
        workflow_details: tuple[str, ...] = (),
        thumbnail: "QImage | None" = None,
        face_records: tuple[object, ...] = (),
        face_preview: "QImage | None" = None,
        category_info: dict[str, object] | None = None,
        category_profile: str = "uncategorized",
    ) -> None:
        if current_record is None:
            self.clear()
            return

        if current_record.is_folder:
            self.set_preview(None, placeholder="Folder")
        elif self._should_use_face_as_main_preview(category_profile, face_records, face_preview):
            self.set_preview(face_preview, placeholder="Face zoom", fill=False)
        else:
            self.set_preview(
                self._smart_focus_preview(thumbnail, category_profile=category_profile),
                placeholder="Preview loading",
                fill=False,
            )
        self.face_cycle_button.setVisible(len(face_records) > 1)
        self.face_cycle_button.setEnabled(len(face_records) > 1)

        decision = self._decision_text(annotation)

        self.culling_rows["Decision"].setText(decision)
        self.culling_rows["Rating"].setText(self._rating_text(annotation))
        self.culling_rows["AI Suggestion"].setText(self._ai_suggestion_text(ai_result))
        self.culling_rows["Confidence"].setText(self._ai_confidence_text(ai_result))
        explanation = build_ai_explanation_lines(ai_result, review_summary=review_summary)
        reason = self._first_text(
            getattr(ai_result, "confidence_summary", "") if ai_result is not None else "",
            getattr(ai_result, "cluster_reason", "") if ai_result is not None else "",
            *(explanation[:2] if ai_result is not None else ()),
            workflow_summary,
        )
        self.culling_rows["Reason"].setText(reason or "-")

        self._set_subject_context(
            category_profile=category_profile,
            category_info=category_info or {},
            face_records=face_records,
            ai_result=ai_result,
        )

        stats = inspection_stats or EMPTY_INSPECTION_STATS
        detail_score = stats.detail_score or self._float_attr(review_insight, "detail_score")
        self.quality_rows["Detail"].setText(self._quality_level(detail_score, stats))
        self.quality_rows["Focus"].setText(self._focus_level(detail_score, stats))
        self.quality_rows["Motion Blur"].setText(self._motion_blur_level(stats.motion_blur_score, analyzed=stats.width > 0, stats=stats))
        self.quality_rows["Noise"].setText(self._noise_level(stats.noise_score, analyzed=stats.width > 0))
        self.quality_rows["Exposure"].setText(self._exposure_label(stats))
        self.quality_rows["Confidence"].setText(self._quality_confidence_label(stats))

        group_size = max(
            int(getattr(ai_result, "group_size", 0) or 0) if ai_result is not None else 0,
            int(getattr(workflow_insight, "group_size", 0) or 0) if workflow_insight is not None else 0,
            current_record.stack_count if current_record.has_variant_stack else 0,
        )
        is_grouped = group_size > 1
        self.group_rows["Group Size"].setText(f"{group_size} images" if is_grouped else "Single image / No similar group")
        self.group_rows["Rank"].setText(ai_result.rank_text if ai_result is not None and ai_result.group_size > 1 else "-")
        best_candidate = self._best_candidate_text(ai_result, workflow_insight)
        self.group_rows["Best Candidate"].setText(best_candidate)
        self.group_rows["Similar Files"].setText(str(max(0, group_size - 1)) if is_grouped else "0")
        duplicate_risk = "High" if bool(getattr(review_insight, "is_duplicate", False)) else ("Low" if is_grouped else "-")
        self.group_rows["Duplicate Risk"].setText(duplicate_risk)
        self.group_rows["Why"].setText(self._first_text(*(workflow_details[:2]), getattr(ai_result, "confidence_summary", "")) or "-")

        self.edit_rows["Worth Editing"].setText(self._worth_editing_text(annotation, ai_result, workflow_insight))
        self.edit_rows["Main Issue"].setText("-")
        self.edit_rows["Fixes Needed"].setText("-")
        self.edit_rows["Effort"].setText("Not analyzed")
        self.edit_rows["Notes"].setText(self._first_text(workflow_summary, review_summary) or "-")

        self.histogram_widget.set_stats(stats)

        self._set_quick_actions_enabled(not current_record.is_folder, grouped=is_grouped)

    @staticmethod
    def _should_use_face_as_main_preview(
        category_profile: str,
        face_records: tuple[object, ...],
        face_preview: "QImage | None",
    ) -> bool:
        return (
            str(category_profile or "").strip().lower() == "people_portrait"
            and bool(face_records)
            and face_preview is not None
            and not face_preview.isNull()
        )

    @staticmethod
    def _smart_focus_preview(thumbnail: "QImage | None", *, category_profile: str) -> "QImage | None":
        if thumbnail is None or thumbnail.isNull():
            return thumbnail
        try:
            result = focus_poi(InspectorPanel._qimage_to_bgr_array(thumbnail))
        except Exception:
            return thumbnail
        if not should_use_smart_focus_crop(category_profile, result):
            return thumbnail
        crop = InspectorPanel._crop_normalized_bbox(thumbnail, result.bbox, padding=0.18)
        return crop if crop is not None and not crop.isNull() else thumbnail

    @staticmethod
    def _qimage_to_bgr_array(image: QImage) -> np.ndarray:
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width = converted.width()
        height = converted.height()
        if width <= 0 or height <= 0:
            return np.empty((0, 0, 3), dtype=np.uint8)
        buffer = converted.constBits()
        try:
            buffer.setsize(converted.sizeInBytes())
        except AttributeError:
            pass
        array = np.frombuffer(buffer, dtype=np.uint8)
        array = array.reshape((height, converted.bytesPerLine()))
        rgba = array[:, : width * 4].reshape((height, width, 4))
        return rgba[:, :, [2, 1, 0]].copy()

    @staticmethod
    def _crop_normalized_bbox(
        image: QImage,
        bbox: tuple[float, float, float, float],
        *,
        padding: float,
    ) -> "QImage | None":
        width = image.width()
        height = image.height()
        if width <= 1 or height <= 1:
            return None
        x0, y0, x1, y1 = bbox
        left = max(0.0, min(1.0, float(x0))) * width
        top = max(0.0, min(1.0, float(y0))) * height
        right = max(0.0, min(1.0, float(x1))) * width
        bottom = max(0.0, min(1.0, float(y1))) * height
        if right <= left or bottom <= top:
            return None

        box_width = right - left
        box_height = bottom - top
        side = max(box_width, box_height) * (1.0 + max(0.0, padding))
        side = max(side, min(width, height) * 0.28)
        side = min(side, min(width, height))
        crop_side = max(1, min(width, height, int(round(side))))
        center_x = (left + right) / 2.0
        center_y = (top + bottom) / 2.0
        crop_left = int(round(max(0.0, min(center_x - crop_side / 2.0, width - crop_side))))
        crop_top = int(round(max(0.0, min(center_y - crop_side / 2.0, height - crop_side))))
        return image.copy(QRect(crop_left, crop_top, crop_side, crop_side))

    def _set_subject_context(
        self,
        *,
        category_profile: str,
        category_info: dict[str, object],
        face_records: tuple[object, ...],
        ai_result: "AIImageResult | None",
    ) -> None:
        profile = str(category_profile or "uncategorized").strip().lower()
        label, focus, signal = self._subject_profile_text(profile)
        self.subject_rows["Type"].setText(label)
        self.subject_rows["Review Focus"].setText(focus)
        if profile == "people_portrait" and face_records:
            primary = max(face_records, key=lambda item: float(getattr(item, "det_score", 0.0) or 0.0))
            det = self._float_attr(primary, "det_score")
            eyes = self._float_attr(primary, "eye_sharpness")
            pieces = [f"{len(face_records)} face{'s' if len(face_records) != 1 else ''}"]
            if det is not None:
                pieces.append(f"{det * 100:.0f}% detect")
            if eyes is not None:
                pieces.append(f"eyes {eyes:.1f}/10")
            signal = " · ".join(pieces)
        self.subject_rows["Signal"].setText(signal)
        confidence = category_info.get("confidence")
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
        confidence_text = f"category score {confidence_value:.2f}" if confidence_value > 0 else ""
        ai_bits = []
        if ai_result is not None:
            bucket = getattr(ai_result, "confidence_bucket_label", "") or getattr(ai_result, "confidence_bucket_short_label", "")
            if bucket:
                ai_bits.append(str(bucket))
            score = getattr(ai_result, "display_score_text", "")
            if score:
                ai_bits.append(str(score))
        if confidence_text:
            ai_bits.append(confidence_text)
        self.subject_rows["AI Detail"].setText(" · ".join(ai_bits) if ai_bits else "-")

    @staticmethod
    def _subject_profile_text(profile: str) -> tuple[str, str, str]:
        mapping = {
            "people_portrait": (
                "Portrait",
                "Face sharpness, expression, eye contact",
                "Use the face zoom first; verify the full frame second.",
            ),
            "landscape": (
                "Landscape",
                "Composition, exposure, edge detail",
                "Check horizon, clipped sky, foreground/background balance.",
            ),
            "wildlife": (
                "Wildlife",
                "Subject sharpness, pose, separation",
                "Confirm the animal is sharp and not hidden by clutter.",
            ),
            "travel_built": (
                "Travel/Built",
                "Geometry, context, clutter",
                "Check leading lines, signs/buildings, and distractions.",
            ),
            "night_astro": (
                "Night/Astro",
                "Noise, exposure, highlight control",
                "Check sky detail, star/city-light clipping, and motion.",
            ),
            "macro_detail": (
                "Macro/Detail",
                "Focus plane, texture, background",
                "Inspect the intended detail at high magnification.",
            ),
            "abstract_texture": (
                "Abstract/Texture",
                "Pattern, contrast, color rhythm",
                "Judge graphic strength before literal subject quality.",
            ),
            "product_still_life": (
                "Product/Still",
                "Lighting, edges, color accuracy",
                "Check specular highlights and object separation.",
            ),
            "street_documentary": (
                "Street/Documentary",
                "Moment, layers, gesture, context",
                "Look for timing, readable story, and distracting overlaps.",
            ),
            "architecture": (
                "Architecture",
                "Lines, symmetry, perspective, detail",
                "Check verticals, edge cleanliness, and compositional balance.",
            ),
            "sports_action": (
                "Sports/Action",
                "Peak action, focus, motion clarity",
                "Prioritize decisive moments, subject sharpness, and separation.",
            ),
            "event_stage": (
                "Event/Stage",
                "Expression, lighting, gesture, atmosphere",
                "Check stage light clipping, faces, and meaningful interaction.",
            ),
            "vehicle_transport": (
                "Vehicle/Transport",
                "Subject angle, motion, environment",
                "Check clean vehicle shape, background clutter, and motion cues.",
            ),
            "interior_space": (
                "Interior",
                "Geometry, light, mood, clutter",
                "Check verticals, mixed lighting, and distracting objects.",
            ),
            "aerial_drone": (
                "Aerial/Drone",
                "Pattern, scale, composition, haze",
                "Look for strong geometry, readable subject scale, and clean edges.",
            ),
            "water_coastal": (
                "Water/Coastal",
                "Reflections, horizon, texture, exposure",
                "Check horizon level, water detail, and highlight control.",
            ),
        }
        return mapping.get(
            profile,
            (
                "General",
                "Detail, exposure, composition",
                "No specialized category context available.",
            ),
        )

    def _set_quick_actions_enabled(self, enabled: bool, *, grouped: bool) -> None:
        for key, button in self.quick_action_buttons.items():
            button.setEnabled(enabled)

    @staticmethod
    def _safe_text(value: object, fallback: str = "-") -> str:
        text = str(value or "").strip()
        return text if text and text.lower() not in {"none", "null"} else fallback

    @staticmethod
    def _first_text(*values: object) -> str:
        for value in values:
            text = str(value or "").strip()
            if text and text.lower() not in {"none", "null"}:
                return text
        return ""

    @staticmethod
    def _float_attr(source: object | None, name: str) -> float | None:
        if source is None:
            return None
        try:
            value = getattr(source, name)
        except AttributeError:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _decision_text(annotation: "SessionAnnotation | None") -> str:
        if annotation is None:
            return "Unreviewed"
        if annotation.winner:
            return "Keep"
        if annotation.reject:
            return "Reject"
        if annotation.rating and annotation.rating >= 3:
            return "Maybe"
        return "Unreviewed"

    @staticmethod
    def _rating_text(annotation: "SessionAnnotation | None") -> str:
        if annotation is None or not annotation.rating:
            return "-"
        return f"{annotation.rating}/5"

    @staticmethod
    def _ai_suggestion_text(ai_result: "AIImageResult | None") -> str:
        if ai_result is None:
            return "No AI result loaded"
        bucket = str(getattr(ai_result, "confidence_bucket", "") or "")
        if "reject" in bucket:
            return "Reject"
        if "winner" in bucket or "keeper" in bucket:
            return "Keep"
        return "Maybe"

    @staticmethod
    def _ai_confidence_text(ai_result: "AIImageResult | None") -> str:
        if ai_result is None:
            return "-"
        normalized = getattr(ai_result, "normalized_score", None)
        try:
            return f"{float(normalized):.0f}%" if normalized is not None else "-"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _quality_level(score: float | None, stats: InspectionStats | None = None) -> str:
        if stats is not None and stats.width > 0 and stats.detail_valid_tile_count < 4:
            return "Low-detail frame"
        if score is None or score <= 0:
            return "Not analyzed"
        if score >= 70:
            return "High"
        if score >= 40:
            return "Moderate"
        return "Low"

    @staticmethod
    def _focus_level(score: float | None, stats: InspectionStats | None = None) -> str:
        if stats is not None and stats.width > 0 and stats.detail_valid_tile_count < 4:
            return "Inconclusive"
        if score is None or score <= 0:
            return "Not analyzed"
        if score >= 70:
            return "Sharp"
        if score >= 40:
            return "Acceptable"
        return "Inconclusive"

    @staticmethod
    def _motion_blur_level(score: float, *, analyzed: bool, stats: InspectionStats | None = None) -> str:
        if not analyzed:
            return "Not analyzed"
        if stats is not None and stats.detail_valid_tile_count < 4:
            return "Not detected"
        if score >= 70:
            return "Possible"
        if score >= 40:
            return "Possible"
        return "Not detected"

    @staticmethod
    def _noise_level(score: float, *, analyzed: bool) -> str:
        if not analyzed:
            return "Not analyzed"
        if score >= 65:
            return "High"
        if score >= 32:
            return "Moderate"
        return "Low"

    @staticmethod
    def _exposure_label(stats: InspectionStats) -> str:
        if stats.width <= 0 or stats.height <= 0:
            return "Not analyzed"
        if stats.highlight_clip_pct >= 2.0 or stats.median_luminance >= 190:
            return "Overexposed"
        if stats.shadow_clip_pct >= 4.0 or stats.median_luminance <= 55:
            return "Underexposed"
        return "Properly exposed"

    @staticmethod
    def _quality_confidence_label(stats: InspectionStats) -> str:
        if stats.width <= 0 or stats.height <= 0:
            return "Not analyzed"
        if stats.detail_valid_tile_count < 4:
            return "Low"
        if stats.detail_confidence >= 70:
            return "Medium"
        return "Low"

    @staticmethod
    def _best_candidate_text(ai_result: "AIImageResult | None", workflow_insight: object | None) -> str:
        if bool(getattr(workflow_insight, "best_in_group", False)):
            return "Yes"
        if ai_result is not None and bool(getattr(ai_result, "is_top_pick", False)):
            return "Yes"
        if ai_result is not None and getattr(ai_result, "group_size", 0) > 1:
            return "No"
        return "-"

    @staticmethod
    def _worth_editing_text(
        annotation: "SessionAnnotation | None",
        ai_result: "AIImageResult | None",
        workflow_insight: object | None,
    ) -> str:
        if annotation is not None:
            if annotation.winner or annotation.rating >= 4:
                return "Yes"
            if annotation.reject:
                return "No"
        if bool(getattr(workflow_insight, "best_in_group", False)):
            return "Yes"
        if ai_result is not None and bool(getattr(ai_result, "is_top_pick", False)):
            return "Yes"
        return "Not analyzed"


def build_workspace_docks(
    window,
    library_panel: QWidget,
    inspector_panel: InspectorPanel,
    center_widget: QWidget,
) -> WorkspaceDocks:
    library = WorkspacePanel(
        "library",
        title="Library",
        subtitle="Favorites and folders",
        side="left",
        variant="library",
        content=library_panel,
        preferred_width=316,
        minimum_width=292,
        maximum_width=460,
    )
    inspector = WorkspacePanel(
        "inspector",
        title="Inspector",
        subtitle="Selection details and quick context",
        side="right",
        variant="inspector",
        content=inspector_panel,
        preferred_width=300,
        minimum_width=300,
        maximum_width=460,
    )

    # Prototype look: floating rounded cards with their own in-content headers,
    # so the panel chrome headers ("Library"/"Inspector") are hidden.
    library.header.setVisible(False)
    inspector.header.setVisible(False)

    shell = QWidget(window)
    shell.setObjectName("workspaceShell")
    shell_layout = QVBoxLayout(shell)
    shell_layout.setContentsMargins(0, 0, 0, 0)
    shell_layout.setSpacing(0)

    splitter = QSplitter(Qt.Orientation.Horizontal, shell)
    splitter.setObjectName("workspaceSplitter")
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(8)
    splitter.addWidget(library)
    splitter.addWidget(center_widget)
    splitter.addWidget(inspector)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setStretchFactor(2, 0)
    shell_layout.addWidget(splitter, 1)

    return WorkspaceDocks(shell, splitter, library, inspector)
