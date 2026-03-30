from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray, QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..formats import suffix_for_path
from .theme import ThemePalette, default_theme

if TYPE_CHECKING:
    from ..ai_results import AIImageResult
    from ..models import ImageRecord, SessionAnnotation


TAB_WIDTH = 34


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
    drag_release_requested = Signal(object)

    def __init__(self, title: str, subtitle: str, *, variant: str, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._floating = False
        self._drag_offset: QPoint | None = None
        self._drag_start: QPoint | None = None
        self._dragging = False
        self.setObjectName(f"{variant}PanelHeader")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("paneTitle")
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
        self._sync_tooltips()

    def _sync_tooltips(self) -> None:
        if self._floating:
            self.minimize_button.setToolTip(f"Minimize {self._title}")
            self.maximize_button.setToolTip(f"Dock {self._title} back into the workspace")
            self.close_button.setToolTip(f"Close {self._title}")
            return
        self.minimize_button.setToolTip(f"Collapse {self._title}")
        self.maximize_button.setToolTip(f"Pop out {self._title}")
        self.close_button.setToolTip(f"Hide {self._title}")
        self.maximize_button.setText("\u25A1")

    def mousePressEvent(self, event) -> None:
        if self._floating and event.button() == Qt.MouseButton.LeftButton:
            top_level = self.window()
            self._drag_offset = event.globalPosition().toPoint() - top_level.frameGeometry().topLeft()
            self._drag_start = event.globalPosition().toPoint()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._floating and self._dragging and self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            top_level = self.window()
            top_level.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        if self._floating and self._drag_offset is not None and self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current = event.globalPosition().toPoint()
            if not self._dragging and (current - self._drag_start).manhattanLength() < QApplication.startDragDistance():
                event.accept()
                return
            self._dragging = True
            top_level = self.window()
            top_level.move(current - self._drag_offset)
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

    def __init__(self, title: str) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(title)
        self.resize(420, 860)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    def set_card(self, widget: QWidget) -> None:
        layout = self.layout()
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
        layout.addWidget(widget)

    def take_card(self) -> QWidget | None:
        layout = self.layout()
        if layout is None or layout.count() == 0:
            return None
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
        return widget

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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.title = title
        self.side = side
        self.variant = variant
        self._expanded_width = preferred_width
        self._minimum_expanded_width = max(236, preferred_width - 44)
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

    def set_expanded_width(self, width: int) -> None:
        if width > 120:
            self._expanded_width = width

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
        self.setMaximumWidth(16777215)

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
        self._panel_map = {
            "library": self.library,
            "inspector": self.inspector,
        }
        self._default_sizes = [self.library.expanded_width, 1240, self.inspector.expanded_width]
        self._wiring_complete = False

        for key, panel in self._panel_map.items():
            panel.collapse_requested.connect(self.collapse_panel)
            panel.popout_requested.connect(self.pop_out_panel)
            panel.close_requested.connect(self.hide_panel)
            panel.expand_requested.connect(self.expand_panel)
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
            "version": 2,
            "splitter_state": _encode_qbytearray(self.splitter.saveState()),
            "panels": panels_state,
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
        self._normalize_panel_sides()
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

    def expand_panel(self, key: str) -> None:
        panel = self._panel_map[key]
        if key in self._floating_windows:
            self.dock_to_side(key, panel.side, show_after=True)
        panel.show_expanded()
        self._set_action_checked(key, True)
        self._rebalance_sizes()

    def collapse_panel(self, key: str) -> None:
        if key in self._floating_windows:
            self._floating_windows[key].showMinimized()
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

    def pop_out_panel(self, key: str, geometry: str | None = None) -> None:
        if key in self._floating_windows:
            self.dock_to_side(key, self._panel_map[key].side, show_after=True)
            return

        panel = self._panel_map[key]
        if panel.mode == "expanded":
            panel.set_expanded_width(panel.width())

        window = FloatingPanelWindow(panel.title)
        window.close_requested.connect(lambda target=key: self._handle_floating_close_request(target))
        window.maximized_changed.connect(lambda maximized, target=key: self._handle_floating_maximize_request(target, maximized))
        window.set_card(panel.detach_frame())
        self._floating_windows[key] = window

        panel.set_floating()
        panel.set_floating_state(True)
        self._set_action_checked(key, True)
        self._rebalance_sizes()

        if geometry:
            floating_geometry = _decode_qbytearray(geometry)
            if not floating_geometry.isEmpty():
                window.restoreGeometry(floating_geometry)
        window.show()
        window.raise_()
        window.activateWindow()

    def dock_to_side(self, key: str, side: str, *, show_after: bool = True) -> None:
        panel = self._panel_map[key]
        if side not in {"left", "right"}:
            side = panel.side
        opposite = "right" if side == "left" else "left"
        for other_key, other_panel in self._panel_map.items():
            if other_key != key and other_panel.side == side:
                other_panel.set_side(opposite)
        panel.set_side(side)
        self._normalize_panel_sides()
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

    def _dock_floating_panel(self, key: str, *, show_after: bool) -> None:
        panel = self._panel_map[key]
        window = self._floating_windows.pop(key, None)
        frame = None
        if window is not None:
            frame = window.take_card()
            window.hide()
            window.deleteLater()
        panel.attach_frame(frame)
        if show_after:
            panel.show_expanded()
            self._set_action_checked(key, True)
        else:
            panel.hide_panel()
            self._set_action_checked(key, False)
        panel.set_floating_state(False)
        self._rebalance_sizes()

    def _handle_floating_close_request(self, key: str) -> None:
        self._dock_floating_panel(key, show_after=False)

    def _handle_floating_maximize_request(self, key: str, maximized: bool) -> None:
        if not maximized or key not in self._floating_windows:
            return
        QTimer.singleShot(0, lambda target=key: self._dock_floating_to_stored_side(target))

    def _handle_floating_drop(self, key: str, global_pos) -> None:
        dock_side = self._resolve_drop_side(global_pos)
        if dock_side is None:
            return
        self.dock_to_side(key, dock_side, show_after=True)

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
        left_panel = self._panel_for_side("left")
        right_panel = self._panel_for_side("right")
        if left_panel.mode == "expanded" and sizes[0] > TAB_WIDTH:
            left_panel.set_expanded_width(sizes[0])
        if right_panel.mode == "expanded" and sizes[2] > TAB_WIDTH:
            right_panel.set_expanded_width(sizes[2])

    def _rebalance_sizes(self) -> None:
        total = max(sum(self.splitter.sizes()), self.splitter.width(), 1200)
        left_panel = self._panel_for_side("left")
        right_panel = self._panel_for_side("right")
        left = 0
        right = 0
        if left_panel.mode == "expanded":
            left = left_panel.expanded_width
        elif left_panel.mode == "collapsed":
            left = TAB_WIDTH
        if right_panel.mode == "expanded":
            right = right_panel.expanded_width
        elif right_panel.mode == "collapsed":
            right = TAB_WIDTH
        center = max(720, total - left - right)
        self.splitter.setSizes([left, center, right])

    def _panel_for_side(self, side: str) -> WorkspacePanel:
        for panel in self._panel_map.values():
            if panel.side == side:
                return panel
        return self.library if side == "left" else self.inspector

    def _normalize_panel_sides(self) -> None:
        sides = {panel.side for panel in self._panel_map.values()}
        if sides == {"left", "right"}:
            return
        self.library.set_side("left")
        self.inspector.set_side("right")

    def _sync_splitter_order(self) -> None:
        left_panel = self._panel_for_side("left")
        right_panel = self._panel_for_side("right")
        self.splitter.insertWidget(0, left_panel)
        if self.center is not None:
            self.splitter.insertWidget(1, self.center)
        self.splitter.insertWidget(2, right_panel)

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

    def _set_action_checked(self, key: str, checked: bool) -> None:
        action = self.toggle_actions[key]
        if action.isChecked() == checked:
            return
        action.blockSignals(True)
        action.setChecked(checked)
        action.blockSignals(False)


class InspectorPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("inspectorPanelContent")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.folder_section = self._make_pair(layout, "Folder")
        self.mode_section = self._make_pair(layout, "Mode")
        self.selection_section = self._make_pair(layout, "Selection")
        self.file_section = self._make_pair(layout, "Current File")
        self.details_section = self._make_pair(layout, "Details")
        self.review_section = self._make_pair(layout, "Review")
        self.ai_section = self._make_pair(layout, "AI")

        self.hint_label = QLabel("Move docks around to shape your workspace. More inspector tools can plug into this panel later.")
        self.hint_label.setObjectName("inspectorHint")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)

        self.clear()

    def _make_pair(self, layout: QVBoxLayout, title: str) -> QLabel:
        label = QLabel(title)
        label.setObjectName("sectionLabel")
        value = QLabel("")
        value.setObjectName("inspectorValue")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        layout.addWidget(value)
        return value

    def clear(self) -> None:
        self.folder_section.setText("No folder selected")
        self.mode_section.setText("Manual Review")
        self.selection_section.setText("Nothing selected")
        self.file_section.setText("Choose an image to see details.")
        self.details_section.setText("Stack, edits, and file format details will appear here.")
        self.review_section.setText("Accepted, rejected, ratings, and tags appear here.")
        self.ai_section.setText("AI details appear when an image has a matched result.")

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
    ) -> None:
        self.folder_section.setText(folder or "No folder selected")
        self.mode_section.setText(mode_label)
        self.selection_section.setText(f"{selected_count} selected" if selected_count else "Nothing selected")

        if current_record is None:
            self.file_section.setText("Choose an image to see details.")
            self.details_section.setText("Stack, edits, and file format details will appear here.")
            self.review_section.setText("Accepted, rejected, ratings, and tags appear here.")
            self.ai_section.setText("AI details appear when an image has a matched result.")
            return

        active_path = display_path or current_record.path
        suffix = suffix_for_path(active_path)
        bundle_text = current_record.bundle_label or suffix[1:].upper() if suffix else "File"
        self.file_section.setText(f"{Path(active_path).name}\n{active_path}")

        detail_parts = [bundle_text]
        if current_record.has_variant_stack:
            detail_parts.append(f"{current_record.stack_count} variants")
        if current_record.has_edits:
            detail_parts.append(f"{len(current_record.edited_paths)} edited")
        if current_record.companion_paths and not current_record.bundle_label:
            detail_parts.append(f"{len(current_record.all_paths)} linked files")
        self.details_section.setText(" | ".join(detail_parts))

        review_parts: list[str] = []
        if annotation is not None:
            if annotation.winner:
                review_parts.append("Accepted")
            if annotation.reject:
                review_parts.append("Rejected")
            if annotation.rating:
                review_parts.append(f"Rating {annotation.rating}/5")
            if annotation.tags:
                review_parts.append(f"Tags: {', '.join(annotation.tags)}")
        self.review_section.setText(" | ".join(review_parts) if review_parts else "Unreviewed")

        if ai_result is None:
            self.ai_section.setText("No AI result loaded for this file.")
            return

        ai_parts = [f"Score {ai_result.display_score_with_scale_text}"]
        if ai_result.group_id:
            ai_parts.append(ai_result.group_id)
        if ai_result.group_size > 1:
            ai_parts.append(ai_result.rank_text)
            if ai_result.is_top_pick:
                ai_parts.append("Top pick")
        self.ai_section.setText(" | ".join(ai_parts))


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
    )
    inspector = WorkspacePanel(
        "inspector",
        title="Inspector",
        subtitle="Selection details and quick context",
        side="right",
        variant="inspector",
        content=inspector_panel,
        preferred_width=296,
    )

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
