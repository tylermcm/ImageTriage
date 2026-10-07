"""The window frame and the display it sits on: maximize and restore, native frame styling, work-area fitting, drag points, display class and style policy, screen changes, the app-bar alignment and glyph/inspector text rendering. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging

from PySide6.QtCore import QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMessageBox, QSizePolicy

from .ui import fit_window_to_available_geometry
from .ui import layout_ratios

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow

_logger = logging.getLogger(__name__)


class DisplayController(QObject):
    """The window frame and the display it sits on: maximize and restore, native frame styling, work-area fitting, drag points, display class and style policy, screen changes, the app-bar alignment and glyph/inspector text rendering. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window

    def sync_window_control_glyphs(self) -> None:
        control = getattr(self._window._toolbar, "_window_control_buttons", {}).get("maximize")
        if control is None:
            return
        maximized = self._window.isMaximized()
        control.setText("\uE923" if maximized else "\uE922")
        control.setToolTip("Restore" if maximized else "Maximize")

    def apply_native_frame_styles(self) -> None:
        """Give the frameless window back a thick frame and caption style so
        Windows still resizes, snaps and animates it; nativeEvent then hides the
        caption area and routes the app bar as the drag region."""
        if not getattr(self._window, "_custom_frame", False):
            return
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = int(self._window.winId())
            gwl_style = -16
            ws_thickframe, ws_caption = 0x00040000, 0x00C00000
            ws_minimizebox, ws_maximizebox, ws_sysmenu = 0x00020000, 0x00010000, 0x00080000
            get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            style = get_style(hwnd, gwl_style)
            set_style(hwnd, gwl_style, (style | ws_thickframe | ws_minimizebox | ws_maximizebox | ws_sysmenu) & ~ws_caption)
            # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010)
        except (AttributeError, OSError, ValueError):
            _logger.warning("Failed to install the custom window frame; disabling it", exc_info=True)
            self._window._custom_frame = False

    def is_window_drag_point(self, local: QPoint) -> bool:
        bar = getattr(self._window, "app_top_bar", None)
        if bar is None or not bar.isVisible():
            return False
        if not bar.geometry().contains(bar.parentWidget().mapFrom(self._window, local)):
            return False
        child = self._window.childAt(local)
        return child in (
            bar,
            getattr(self._window._toolbar, "app_menu_slot", None),
            getattr(self._window._toolbar, "app_crumb_stack", None),
            getattr(self._window._toolbar, "app_breadcrumb", None),
            getattr(self._window, "central_container", None),
        )

    def align_app_bar_to_library(self) -> None:
        """Centre Menu over the rail and start the breadcrumb at the library
        pane's content edge; the spacer is corrected from the measured position
        after each layout pass."""
        self._app_bar_align_pending = False
        bar = getattr(self._window, "app_top_bar", None)
        rail = getattr(self._window, "left_nav_rail", None)
        pages = getattr(self._window, "left_nav_pages", None)
        if bar is None or rail is None or pages is None or not bar.isVisible():
            return
        layout = bar.layout()
        origin = bar.mapTo(self._window, QPoint(0, 0)).x()
        menu_width = self._window._toolbar.app_menu_button.sizeHint().width()
        if rail.isVisible() and rail.width() > 0:
            slot_x = rail.mapTo(self._window, QPoint(0, 0)).x() - origin
            slot_width = max(menu_width, rail.width())
            crumb_target = pages.mapTo(self._window, QPoint(0, 0)).x() - origin + self._window.APP_BREADCRUMB_INSET
        else:
            slot_x = layout.contentsMargins().left()
            slot_width = menu_width
            crumb_target = slot_x + menu_width + 12
        self._window._toolbar.app_menu_slot.setGeometry(slot_x, 0, slot_width, bar.height())
        self._window._toolbar.app_menu_slot.raise_()
        current = self._window._toolbar._app_bar_crumb_spacer.sizeHint().width()
        gap = max(0, current + crumb_target - self._window._toolbar.app_crumb_stack.x())
        if gap != current:
            self._window._toolbar._app_bar_crumb_spacer.changeSize(gap, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            layout.invalidate()
            layout.activate()
            self.schedule_app_bar_alignment()

    def schedule_app_bar_alignment(self) -> None:
        if getattr(self, "_app_bar_align_pending", False):
            return
        self._app_bar_align_pending = True
        QTimer.singleShot(0, self.align_app_bar_to_library)

    def display_class(self) -> str:
        # Card detail depends chiefly on usable height. A narrow window should
        # condense its chrome without unexpectedly forcing photo-only cards.
        screen = self._window.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return "high"
        height = screen.availableGeometry().height()
        if height <= 800:
            return "low"
        if height <= 1200:
            return "medium"
        return "high"

    def allowed_card_styles(self, display_class: str | None = None) -> tuple[str, ...]:
        cls = display_class or self.display_class()
        return tuple(self._window._DISPLAY_STYLE_POLICY[cls]["styles"])

    def effective_card_style(self, saved: str, display_class: str) -> str:
        policy = self._window._DISPLAY_STYLE_POLICY[display_class]
        return saved if saved in policy["styles"] else policy["default"]

    def apply_display_style_policy(self, *, show_warning: bool) -> None:
        """Coerce the effective card style + column thresholds to what the
        current display can show. The user's saved preference is left untouched,
        so it comes back when they move to a larger display."""
        display_class = self.display_class()
        policy = self._window._DISPLAY_STYLE_POLICY[display_class]
        effective = self.effective_card_style(self._window._loupe_card_style, display_class)
        self._window._display_class_value = display_class
        self._window._effective_loupe_card_style = effective
        if getattr(self._window, "grid", None) is not None:
            self._window.grid.set_loupe_card_style(effective)
            self._window.grid.set_column_style_thresholds(policy["compact_threshold"], policy["plain_threshold"])
        if show_warning and display_class == "low":
            self.maybe_warn_small_display()

    def set_grid_filenames_visible(self, visible: bool) -> None:
        target_style = "gallery" if visible else "zen"
        if self._window._loupe_card_style == target_style and self._window._effective_loupe_card_style == target_style:
            return
        self._window._loupe_card_style = target_style
        self._window._settings.setValue(self._window.LOUPE_CARD_STYLE_KEY, target_style)
        self.apply_display_style_policy(show_warning=False)
        self._window.statusBar().showMessage("Filenames shown" if visible else "Filenames hidden")

    def post_show_display_setup(self) -> None:
        # Runs once the window is up: warn if on a small display, and re-apply the
        # policy live when moved to another screen or the resolution changes.
        self._window._appearance.apply_display_profile()
        self.apply_display_style_policy(show_warning=True)
        handle = self._window.windowHandle()
        if handle is not None:
            handle.screenChanged.connect(self.handle_display_change)
        self.connect_screen_geometry_signal()

    def connect_screen_geometry_signal(self) -> None:
        screen = self._window.screen()
        if screen is None:
            return
        try:
            screen.geometryChanged.connect(
                self.handle_display_change, Qt.ConnectionType.UniqueConnection
            )
            screen.availableGeometryChanged.connect(
                self.handle_display_change, Qt.ConnectionType.UniqueConnection
            )
            screen.logicalDotsPerInchChanged.connect(
                self.handle_display_change, Qt.ConnectionType.UniqueConnection
            )
        except (TypeError, RuntimeError):
            pass

    def handle_display_change(self, _arg=None) -> None:
        self.connect_screen_geometry_signal()
        self._window._appearance.schedule_display_profile_update()
        QTimer.singleShot(0, lambda: fit_window_to_available_geometry(self._window))
        self.apply_display_style_policy(show_warning=True)

    def maybe_warn_small_display(self) -> None:
        if self._window._settings.value(self._window.SMALL_DISPLAY_WARNED_KEY, False, bool):
            return
        self._window._settings.setValue(self._window.SMALL_DISPLAY_WARNED_KEY, True)
        screen = self._window.screen() or QGuiApplication.primaryScreen()
        size = screen.size() if screen is not None else None
        dims = f"{size.width()}×{size.height()}" if size is not None else "small"
        QMessageBox.information(
            self._window,
            "Small display detected",
            f"Your screen ({dims}) is small, so the card style is locked to Zen "
            "(photo-only) to keep the layout usable. Other card styles become "
            "available again on a larger display.",
        )

    def apply_inspector_text_ratios(self, width: int, height: int) -> None:
        """Size the inspector's own text and its label column.

        One sheet on the panel root, which every row inherits, so rows built
        later for a new photo pick the sizes up without being visited.
        """
        panel = getattr(self._window, "inspector_panel", None)
        if panel is None:
            return
        px = layout_ratios.ratio_px
        floor = layout_ratios.MIN_TEXT_PX
        title = px(layout_ratios.INSPECTOR_TITLE_H, height, minimum=floor)
        aside = px(layout_ratios.INSPECTOR_ASIDE_H, height, minimum=floor)
        key = px(layout_ratios.INSPECTOR_KEY_H, height, minimum=floor)
        value = px(layout_ratios.INSPECTOR_VALUE_H, height, minimum=floor)
        preview = px(layout_ratios.INSPECTOR_PREVIEW_H, height, minimum=floor)
        # The label column is a share of the pane, so it keeps its proportion
        # as the pane is resized rather than eating a narrow inspector.
        pane = panel.width() or px(layout_ratios.INSPECTOR_W, width, minimum=200)
        key_width = max(48, round(layout_ratios.INSPECTOR_KEY_W * pane))
        chip = px(layout_ratios.INSPECTOR_CHIP_TEXT_H, height, minimum=floor)
        nav = px(layout_ratios.INSPECTOR_NAV_H, height, minimum=layout_ratios.MIN_GLYPH_PX)
        analyze = px(layout_ratios.ANALYZE_BUTTON_H, height, minimum=20)
        sheet = (
            f"QLabel#inspectorAiChip {{ font-size: {chip}px; }}"
            f"QToolButton#inspectorNavButton {{ font-size: {nav}px; }}"
            f"QPushButton#inspectorAnalyzeButton {{ min-height: {analyze}px; }}"
            f"QLabel#inspectorSectionTitle {{ font-size: {title}px; }}"
            f"QLabel#inspectorSectionAside {{ font-size: {aside}px; }}"
            f"QLabel#inspectorKey {{ font-size: {key}px;"
            f" min-width: {key_width}px; max-width: {key_width}px; }}"
            f"QLabel#inspectorValue {{ font-size: {value}px; }}"
            f"QLabel#inspectorPreviewName, QLabel#inspectorPreviewPosition"
            f" {{ font-size: {preview}px; }}"
        )
        if panel.styleSheet() != sheet:
            panel.setStyleSheet(sheet)
