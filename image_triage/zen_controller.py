"""Zen mode (full-screen review with the chrome hidden): entering and leaving it, the hint overlay and the pop-up menu. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.5)."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QObject, QPoint, QRect, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class ZenController(QObject):
    """Zen mode (full-screen review with the chrome hidden): entering and leaving it, the hint overlay and the pop-up menu. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.5)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._zen_menu_visible = False
        self._zen_restore_state: dict[str, object] = {}

    def handle_zen_mode_toggled(self, checked: bool) -> None:
        self.set_zen_mode(bool(checked))

    def handle_zen_toggle_shortcut(self) -> None:
        if self._window._collection_mode:
            return
        self.set_zen_mode(not self._window._zen_mode_enabled)

    def handle_zen_escape_shortcut(self) -> None:
        if self._window._zen_mode_enabled:
            self.set_zen_mode(False)

    def position_zen_hint_overlay(self) -> None:
        if not hasattr(self._window, "zen_hint_overlay") or not hasattr(self._window, "central_container"):
            return
        hint = self._window.zen_hint_overlay
        hint.adjustSize()
        width = max(250, hint.width() + 28)
        height = max(34, hint.height() + 10)
        x = max(12, (self._window.central_container.width() - width) // 2)
        y = 18
        hint.setGeometry(x, y, width, height)

    def show_zen_hint_overlay(self) -> None:
        if not self._window._zen_mode_enabled or not hasattr(self._window, "zen_hint_overlay"):
            return
        self.position_zen_hint_overlay()
        self._window.zen_hint_overlay.show()
        self._window.zen_hint_overlay.raise_()
        self._window.zen_hint_hide_timer.start(1800)

    def handle_zen_menu_pin_toggled(self, checked: bool) -> None:
        self._window._zen_menu_pinned = bool(checked)
        self._window._settings.setValue(self._window.ZEN_MENU_PINNED_KEY, self._window._zen_menu_pinned)
        if self._window._zen_mode_enabled:
            self.set_zen_menu_visible(self._window._zen_menu_pinned)

    def set_zen_menu_visible(self, visible: bool) -> None:
        visible = bool(visible)
        previous_visible = self._zen_menu_visible
        self._zen_menu_visible = visible
        if not self._window._zen_mode_enabled:
            return
        menu_bar = self._window.menuBar()
        target_height = max(28, menu_bar.sizeHint().height()) if visible else 0
        if previous_visible == visible and menu_bar.maximumHeight() == target_height:
            return
        if visible:
            menu_bar.setMinimumHeight(0)
            menu_bar.show()
        if hasattr(self._window, "zen_menu_pin_button"):
            self._window.zen_menu_pin_button.setVisible(True)
        current_height = max(0, menu_bar.height() if menu_bar.isVisible() else 0)
        self._window._zen_menu_animation.stop()
        menu_bar.setMaximumHeight(current_height)
        self._window._zen_menu_animation.setStartValue(current_height)
        self._window._zen_menu_animation.setEndValue(target_height)
        self._window._zen_menu_animation.start()

    def refresh_zen_menu_visibility(self) -> None:
        if not self._window._zen_mode_enabled:
            self._window._zen_menu_reveal_timer.stop()
            return
        if self._window._zen_menu_pinned or QApplication.activePopupWidget() is not None:
            self.set_zen_menu_visible(True)
            return
        local_pos = self._window.mapFromGlobal(QCursor.pos())
        if not QRect(QPoint(0, 0), self._window.size()).contains(local_pos):
            self.set_zen_menu_visible(False)
            return
        menu_height = max(28, self._window.menuBar().sizeHint().height())
        if local_pos.y() <= 8:
            self.set_zen_menu_visible(True)
        elif self._zen_menu_visible and local_pos.y() > menu_height + 10:
            self.set_zen_menu_visible(False)

    def set_zen_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled and self._window._collection_mode:
            return
        if self._window._zen_mode_enabled == enabled:
            self._window._inspector.update_action_states()
            return
        if enabled:
            self._zen_restore_state = {
                "window_state": self._window.windowState(),
                "geometry": self._window.saveGeometry(),
                "maximized": self._window.isMaximized(),
                "fullscreen": self._window.isFullScreen(),
                "menu_visible": self._window.menuBar().isVisible(),
                "status_visible": self._window.statusBar().isVisible(),
                "workspace_bar_visible": self._window.workspace_bar.isVisible(),
                "tool_mode_bar_visible": self._window.tool_mode_bar.isVisible(),
                "workspace_state": self._window.workspace_docks.save_state() if self._window.workspace_docks is not None else None,
            }
            self._window._zen_mode_enabled = True
            self._window.menuBar().setMinimumHeight(0)
            if hasattr(self._window, "zen_menu_pin_button"):
                with QSignalBlocker(self._window.zen_menu_pin_button):
                    self._window.zen_menu_pin_button.setChecked(self._window._zen_menu_pinned)
            self.set_zen_menu_visible(self._window._zen_menu_pinned)
            self._window._zen_menu_reveal_timer.start()
            self._window._zen_escape_shortcut.setEnabled(True)
            self._window.statusBar().hide()
            self._window.workspace_bar.hide()
            self._window.tool_mode_bar.hide()
            if self._window.workspace_docks is not None:
                self._window.workspace_docks.hide_panel("library")
                self._window.workspace_docks.hide_panel("inspector")
            self._window.showFullScreen()
            QTimer.singleShot(120, self.show_zen_hint_overlay)
            self._window.statusBar().showMessage("Zen Mode enabled")
        else:
            restore_state = self._zen_restore_state or {}
            self._window._zen_mode_enabled = False
            self._window._zen_menu_reveal_timer.stop()
            self._window._zen_menu_animation.stop()
            self._window._zen_escape_shortcut.setEnabled(False)
            if hasattr(self._window, "zen_menu_pin_button"):
                self._window.zen_menu_pin_button.hide()
            if hasattr(self._window, "zen_hint_overlay"):
                self._window.zen_hint_hide_timer.stop()
                self._window.zen_hint_overlay.hide()
            self._window.menuBar().setMinimumHeight(0)
            self._window.menuBar().setMaximumHeight(16777215)
            window_state = restore_state.get("window_state")
            geometry = restore_state.get("geometry")
            if bool(restore_state.get("fullscreen", False)):
                pass
            elif bool(restore_state.get("maximized", False)):
                self._window.showMaximized()
            else:
                self._window.showNormal()
                if isinstance(geometry, QByteArray) and not geometry.isEmpty():
                    self._window.restoreGeometry(geometry)
                elif isinstance(window_state, Qt.WindowState):
                    self._window.setWindowState(Qt.WindowState(window_state.value & ~Qt.WindowState.WindowFullScreen.value))
            self._window.menuBar().setVisible(bool(restore_state.get("menu_visible", True)))
            self._window.statusBar().setVisible(bool(restore_state.get("status_visible", True)))
            self._window.workspace_bar.setVisible(bool(restore_state.get("workspace_bar_visible", True)))
            self._window.tool_mode_bar.setVisible(bool(restore_state.get("tool_mode_bar_visible", True)))
            workspace_state = restore_state.get("workspace_state")
            if self._window.workspace_docks is not None and isinstance(workspace_state, dict):
                self._window.workspace_docks.restore_state(workspace_state)
            self._zen_restore_state = {}
            self._window.statusBar().showMessage("Zen Mode disabled")
        self._window._inspector.update_action_states()
