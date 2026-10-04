"""The top bar and the workspace toolbar: building and rebuilding their buttons, slots and layouts, overflow handling, pinned tools, placement and position. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.5)."""
from __future__ import annotations

import json
import os
import time

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QMenu, QSizePolicy, QSlider, QSpacerItem, QStackedWidget, QToolButton, QVBoxLayout, QWidget
from collections.abc import Callable
from dataclasses import replace

from .perf import perf_logger
from .ui import WORKSPACE_METRICS, default_theme
from .ui import layout_ratios
from .ui.breadcrumb import BreadcrumbBar
from .ui.display_metrics import DisplayProfile, STANDARD_DISPLAY
from .ui.topbar_sync import _TopbarActionSync

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class ToolbarController(QObject):
    """The top bar and the workspace toolbar: building and rebuilding their buttons, slots and layouts, overflow handling, pinned tools, placement and position. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.5)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._workspace_bar_drag_start: QPoint | None = None
        self._workspace_bar_dragging = False
        self._workspace_toolbar_hidden_items: dict[str, tuple[str, ...]] = {}
        self._workspace_toolbar_overflow_menus: dict[str, QMenu] = {}
        self._workspace_toolbar_overflow_update_pending: set[str] = set()

    def build_prototype_top_bar(self) -> QWidget:
        """The prototype-style primary top bar (nav + search + panel toggles).

        The centre is intentionally left empty; the backend action buttons will
        be placed there in a later migration step.
        """
        self._window._nav_back: list[str] = []
        self._window._nav_forward: list[str] = []
        self._nav_suppress_history = False

        bar = QWidget()
        bar.setObjectName("appTopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 6, 10, 6)
        layout.setSpacing(WORKSPACE_METRICS.space_6)

        self._topbar_nav_buttons: list[tuple[QToolButton, int]] = []
        self._topbar_labeled_nav_buttons: list[tuple[QToolButton, str]] = []

        def make_icon_button(glyph: str, tooltip: str) -> QToolButton:
            button = QToolButton(bar)
            button.setObjectName("appTopBarButton")
            button.setText(glyph)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setAutoRaise(True)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setFixedSize(34, 34)
            button.setToolTip(tooltip)
            font = button.font()
            font.setPixelSize(16)
            button.setFont(font)
            self._topbar_nav_buttons.append((button, 16))
            return button

        nav_cluster = QWidget(bar)
        nav_cluster.setObjectName("topbarNavCluster")
        nav_layout = QHBoxLayout(nav_cluster)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(self._window.TOPBAR_SLOT_SPACING)
        self._topbar_nav_layout = nav_layout

        def make_labeled_nav_button(item_id: str, label: str, tooltip: str) -> QToolButton:
            button = QToolButton(nav_cluster)
            button.setText(label)
            button.setToolTip(tooltip)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self.apply_topbar_button_style(button, self.topbar_nav_icon(item_id))
            self._topbar_labeled_nav_buttons.append((button, item_id))
            nav_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            return button

        # Menu floats centred over the navigation rail (it is placed by hand,
        # outside the layout) and a spacer starts the breadcrumb at the library
        # pane's edge; _align_app_bar_to_library keeps both lined up.
        self._window.app_menu_slot = QWidget(bar)
        menu_slot_layout = QHBoxLayout(self._window.app_menu_slot)
        menu_slot_layout.setContentsMargins(0, 0, 0, 0)
        self.app_menu_button = QToolButton(self._window.app_menu_slot)
        self.app_menu_button.setObjectName("appMenuButton")
        # Icon only: the text is kept for the tooltip and screen readers.
        self.app_menu_button.setText("Menu")
        self.app_menu_button.setToolTip("Menu")
        self.app_menu_button.setAccessibleName("Menu")
        self.app_menu_button.setIcon(self.topbar_nav_icon("menu"))
        self.app_menu_button.setIconSize(QSize(16, 16))
        self.app_menu_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.app_menu_button.setAutoRaise(True)
        self.app_menu_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.app_menu_button.clicked.connect(
            lambda _checked=False: self.show_main_menu_popup(self.app_menu_button)
        )
        menu_slot_layout.addWidget(self.app_menu_button, 0, Qt.AlignmentFlag.AlignCenter)
        self._app_bar_crumb_spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        layout.addItem(self._app_bar_crumb_spacer)

        self._window.app_crumb_stack = QStackedWidget(bar)
        self._window.app_crumb_stack.setObjectName("appBreadcrumbStack")
        self._window.app_crumb_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._window.app_breadcrumb = BreadcrumbBar(self._window.app_crumb_stack)
        self._window.app_breadcrumb.segment_clicked.connect(self.handle_breadcrumb_segment_clicked)
        self._window.app_breadcrumb.edit_requested.connect(self.begin_breadcrumb_path_edit)
        self._window.app_crumb_stack.addWidget(self._window.app_breadcrumb)
        layout.addWidget(self._window.app_crumb_stack, 1)

        open_button = make_labeled_nav_button("open", "Open", self._window.actions.open_folder.toolTip())
        open_button.clicked.connect(lambda _checked=False: self._window.actions.open_folder.trigger())
        self._window.actions.open_folder.changed.connect(
            lambda target=open_button, action=self._window.actions.open_folder: target.setToolTip(action.toolTip())
        )

        self._window._topbar_back_button = make_labeled_nav_button("back", "Back", "Back")
        self._window._topbar_back_button.clicked.connect(lambda: self.navigate_history(-1))
        self._window._topbar_back_button.setEnabled(False)

        self._window._topbar_forward_button = make_labeled_nav_button("forward", "Fwd", "Forward")
        self._window._topbar_forward_button.clicked.connect(lambda: self.navigate_history(1))
        self._window._topbar_forward_button.setEnabled(False)

        self._window._topbar_up_button = make_labeled_nav_button("up", "Up", "Open parent folder")
        self._window._topbar_up_button.clicked.connect(self._window._navigate_to_parent_folder)

        refresh_button = make_labeled_nav_button("refresh", "Refresh", self._window.actions.refresh_folder.toolTip())
        refresh_button.clicked.connect(lambda _checked=False: self._window.actions.refresh_folder.trigger())
        self._window.actions.refresh_folder.changed.connect(
            lambda target=refresh_button, action=self._window.actions.refresh_folder: target.setToolTip(action.toolTip())
        )

        undo_button = make_labeled_nav_button("undo", "Undo", self._window.actions.undo.toolTip())
        undo_button.clicked.connect(lambda _checked=False: self._window.actions.undo.trigger())
        self._window.actions.undo.changed.connect(
            lambda target=undo_button, action=self._window.actions.undo: target.setToolTip(action.toolTip())
        )

        self.topbar_search_field = self._window._build_search_field()
        self.topbar_search_field.setMinimumWidth(180)
        self.topbar_search_field.setMaximumWidth(16777215)
        self.topbar_search_field.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.topbar_search_field.textChanged.connect(
            lambda text: self._window._records_view.handle_search_text_changed(text, source="topbar")
        )
        self.app_search_box = QFrame(bar)
        self.app_search_box.setObjectName("appSearchBox")
        self.app_search_box.setMaximumWidth(self._window.APP_SEARCH_MAX_WIDTH)
        self.app_search_box.setMinimumWidth(240)
        self.app_search_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        search_layout = QHBoxLayout(self.app_search_box)
        search_layout.setContentsMargins(10, 0, 6, 0)
        search_layout.setSpacing(6)
        search_glyph = QLabel("\uE721", self.app_search_box)
        search_glyph.setObjectName("appSearchGlyph")
        search_layout.addWidget(search_glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        search_layout.addWidget(self.topbar_search_field, 1)
        palette_hint = QToolButton(self.app_search_box)
        palette_hint.setObjectName("appSearchKeyHint")
        palette_hint.setText("Ctrl K")
        palette_hint.setToolTip("Command palette (Ctrl+K)")
        palette_hint.setAutoRaise(True)
        palette_hint.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        palette_hint.setCursor(Qt.CursorShape.PointingHandCursor)
        palette_hint.clicked.connect(lambda _checked=False: self._window._open_command_palette(context="main"))
        search_layout.addWidget(palette_hint, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.app_search_box, 1)
        update_button = getattr(self._window, "update_download_button", None)
        if update_button is not None:
            layout.addWidget(update_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.app_settings_button = QToolButton(bar)
        self.app_settings_button.setObjectName("appSettingsButton")
        self.app_settings_button.setToolTip("Settings")
        self.app_settings_button.setProperty("fluentGlyph", "E713")
        self.app_settings_button.setIcon(self._window._appearance.fluent_filled_icon("E713", self._window._appearance.chrome_icon_color()))
        self.app_settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.app_settings_button.setAutoRaise(True)
        self.app_settings_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.app_settings_button.setFixedSize(32, 32)
        self.app_settings_button.clicked.connect(lambda _checked=False: self._window._show_settings())
        self._window._left_settings_buttons.append((self.app_settings_button, 18))
        layout.addWidget(self.app_settings_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._window._window_control_buttons: dict[str, QToolButton] = {}
        if getattr(self._window, "_custom_frame", False):
            layout.addSpacing(6)
            for key, glyph, tooltip, handler in (
                ("minimize", "\uE921", "Minimize", self._window.showMinimized),
                ("maximize", "\uE922", "Maximize", self.toggle_maximized),
                ("close", "\uE8BB", "Close", self._window.close),
            ):
                control = QToolButton(bar)
                control.setObjectName("appWindowCloseButton" if key == "close" else "appWindowButton")
                control.setText(glyph)
                control.setToolTip(tooltip)
                control.setAutoRaise(True)
                control.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                control.clicked.connect(lambda _checked=False, target=handler: target())
                self._window._window_control_buttons[key] = control
                layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)

        self._window.topbar_action_stack = self.build_topbar_action_stack()
        self.toolbar_strip = self.build_toolbar_strip(nav_cluster, self._window.topbar_action_stack)

        # Zoom and the panel toggles travel together: into the status bar when
        # the toolbar floats, onto the end of the docked toolbar otherwise.
        self.view_controls = QWidget()
        self.view_controls.setObjectName("viewControls")
        self.view_controls.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        view_controls_layout = QHBoxLayout(self.view_controls)
        view_controls_layout.setContentsMargins(0, 0, 0, 0)
        view_controls_layout.setSpacing(6)

        # Thumbnail zoom slider (drives the grid column count: left =
        # more/smaller, right = fewer/larger).
        zoom_cluster = QWidget(self.view_controls)
        zoom_cluster.setObjectName("topbarZoomCluster")
        # The toolbar-edit HUD anchors itself just left of this cluster.
        self.topbar_zoom_cluster = zoom_cluster
        zoom_layout = QHBoxLayout(zoom_cluster)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(7)
        zoom_small = QLabel("⌕", zoom_cluster)
        zoom_small.setObjectName("topbarZoomIconSmall")
        zoom_large = QLabel("⌕", zoom_cluster)
        zoom_large.setObjectName("topbarZoomIconLarge")
        self._window.topbar_zoom_slider = QSlider(Qt.Orientation.Horizontal, zoom_cluster)
        self._window.topbar_zoom_slider.setObjectName("topbarZoomSlider")
        self._window.topbar_zoom_slider.setRange(0, 100)
        self._window.topbar_zoom_slider.setSingleStep(1)
        self._window.topbar_zoom_slider.setPageStep(12)
        self._window.topbar_zoom_slider.setTickPosition(QSlider.TickPosition.NoTicks)
        self._window.topbar_zoom_slider.setFixedWidth(118)
        self._window.topbar_zoom_slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._window.topbar_zoom_slider.setToolTip("Thumbnail size")
        self._window.topbar_zoom_slider.setValue(self.initial_zoom_level())
        self._window.topbar_zoom_slider.valueChanged.connect(self.handle_zoom_slider_changed)
        zoom_layout.addWidget(zoom_small, 0)
        zoom_layout.addWidget(self._window.topbar_zoom_slider, 0)
        zoom_layout.addWidget(zoom_large, 0)
        view_controls_layout.addWidget(zoom_cluster, 0)
        view_controls_layout.addSpacing(6)

        # Directory bar takes the zoom's old (right) position.
        self._window.topbar_path_combo = self._window._build_path_combo(mode="topbar")
        self._window.topbar_path_combo.setMinimumWidth(220)
        self._window.topbar_path_combo.setMaximumWidth(460)
        self._window.topbar_path_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        _topbar_path_line_edit = (
            self._window.topbar_path_combo.lineEdit() if hasattr(self._window.topbar_path_combo, "lineEdit") else None
        )
        if _topbar_path_line_edit is not None:
            _topbar_path_line_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            _topbar_path_line_edit.installEventFilter(self._window)
            _topbar_path_line_edit.returnPressed.connect(
                lambda: QTimer.singleShot(0, self._window._appearance.end_breadcrumb_path_edit)
            )
        self._window.topbar_path_combo.setMaximumWidth(16777215)
        self._window.topbar_path_combo.activated.connect(lambda _index: QTimer.singleShot(0, self._window._appearance.end_breadcrumb_path_edit))
        # The editable path box hides behind the breadcrumb until asked for.
        self._window.app_crumb_stack.addWidget(self._window.topbar_path_combo)
        self._window.app_crumb_stack.setCurrentWidget(self._window.app_breadcrumb)

        self._topbar_pane_buttons: dict[str, QToolButton] = {}
        for side, tooltip, key in (
            ("left", "Show or hide the library panel", "library"),
            ("right", "Show or hide the inspector panel", "inspector"),
        ):
            toggle = QToolButton(self.view_controls)
            toggle.setObjectName("appTopBarPaneButton")
            toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            toggle.setAutoRaise(True)
            toggle.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            toggle.setFixedSize(38, 38)
            toggle.setIconSize(QSize(24, 24))
            toggle.setIcon(self._window._appearance.pane_toggle_icon(side))
            toggle.setToolTip(tooltip)
            toggle.setAccessibleName(tooltip)
            action = self._window.workspace_docks.toggle_actions.get(key)
            if action is not None:
                toggle.setCheckable(True)
                toggle.setChecked(action.isChecked())
                toggle.clicked.connect(lambda _checked=False, target=action: target.trigger())
                action.toggled.connect(toggle.setChecked)
            self._topbar_pane_buttons[key] = toggle
            view_controls_layout.addWidget(toggle)

        for watched in (self._window.left_nav_rail, self._window.left_nav_pages, bar):
            watched.installEventFilter(self._window)
        return bar

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def toolbar_profile(self) -> DisplayProfile:
        """Button metrics for the customizable bar. Floating, the buttons grow
        to fill the dock's proportional height while still fitting its width."""
        profile = getattr(self._window, "_display_profile", None) or STANDARD_DISPLAY
        if getattr(self._window, "_toolbar_placement", "docked") != "floating" or self._window.height() <= 0:
            return profile
        margins = getattr(self, "_toolbar_strip_layout", None)
        vertical = (margins.contentsMargins().top() + margins.contentsMargins().bottom()) if margins else 8
        button_height = layout_ratios.ratio_px(layout_ratios.FLOATING_TOOLBAR_H, self._window.height()) - vertical - 2 * profile.topbar_hover_margin
        height_scale = max(1.0, button_height / max(1, profile.topbar_button_height))
        items = len(getattr(self, "_topbar_labeled_nav_buttons", ())) + max(1, self.used_toolbar_slot_count())
        dock_width = layout_ratios.ratio_px(layout_ratios.FLOATING_TOOLBAR_W, self._window.width())
        cell = max(profile.topbar_slot_cell_min, profile.topbar_slot_button_width + 2 * profile.topbar_hover_margin)
        width_scale = max(1.0, (dock_width - 40) / max(1, items * (cell + profile.topbar_slot_spacing)))
        scale = min(height_scale, width_scale)
        return replace(
            profile,
            topbar_button_height=max(profile.topbar_button_height, round(button_height)),
            topbar_slot_button_width=round(profile.topbar_slot_button_width * scale),
            topbar_slot_cell_min=round(profile.topbar_slot_cell_min * scale),
            topbar_glyph_size=round(profile.topbar_glyph_size * scale),
            topbar_caption_height=round(profile.topbar_caption_height * scale),
        )

    def used_toolbar_slot_count(self) -> int:
        used = 0
        for index, item_id in enumerate(getattr(self._window, "_topbar_slots", {}).get("manual") or []):
            if item_id:
                used = index + 1
        return used

    def handle_breadcrumb_segment_clicked(self, path: str) -> None:
        if path and os.path.normcase(os.path.normpath(path)) != os.path.normcase(os.path.normpath(self._window._current_folder or "")):
            self._window._select_folder(path)

    def begin_breadcrumb_path_edit(self) -> None:
        combo = self._window.topbar_path_combo
        self._window.app_crumb_stack.setCurrentWidget(combo)
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setFocus(Qt.FocusReason.MouseFocusReason)
            line_edit.selectAll()

    def size_view_controls(self) -> None:
        """Compact zoom + panel toggles for the status bar; full size on the
        docked toolbar."""
        controls = getattr(self, "view_controls", None)
        if controls is None:
            return
        compact = self._window._toolbar_placement == "floating"
        profile = getattr(self._window, "_display_profile", None) or STANDARD_DISPLAY
        # Status-bar sizes come from layout_ratios (STATUS_*, ZOOM_*); docked on
        # the toolbar, the controls take the toolbar's own button metrics.
        px = layout_ratios.ratio_px
        height = max(1, self._window.height())
        floor = layout_ratios.MIN_GLYPH_PX
        toggle_box = px(layout_ratios.STATUS_TOGGLE_BOX_H, height, minimum=16)
        toggle_icon = px(layout_ratios.STATUS_TOGGLE_ICON_H, height, minimum=floor)
        for button in getattr(self, "_topbar_pane_buttons", {}).values():
            if compact:
                button.setFixedSize(toggle_box, toggle_box)
                button.setIconSize(QSize(toggle_icon, toggle_icon))
            else:
                hover_width = profile.topbar_slot_button_width + 2 * profile.topbar_hover_margin
                hover_height = profile.topbar_button_height + 2 * profile.topbar_hover_margin
                button.setFixedSize(hover_width, hover_height)
                button.setIconSize(QSize(profile.topbar_glyph_size + 2, profile.topbar_glyph_size + 2))
        slider = getattr(self._window, "topbar_zoom_slider", None)
        if slider is not None:
            slider_width = px(layout_ratios.ZOOM_SLIDER_W, max(1, self._window.width()), minimum=48)
            slider.setFixedWidth(slider_width if compact else profile.topbar_zoom_width)

        # The slider's line and handle, and the magnifiers either side of it.
        track = px(layout_ratios.ZOOM_TRACK_H, height, minimum=1)
        handle = px(layout_ratios.ZOOM_HANDLE_H, height, minimum=4)
        sheet = (
            "QSlider#topbarZoomSlider::groove:horizontal"
            f" {{ height: {track}px; border-radius: {max(0, track // 2)}px; }}"
            " QSlider#topbarZoomSlider::handle:horizontal"
            f" {{ width: {handle}px; height: {handle}px;"
            f" margin: -{max(0, (handle - track) // 2)}px 0px;"
            f" border-radius: {handle // 2}px; }}"
            " QLabel#topbarZoomIconSmall"
            f" {{ font-size: {px(layout_ratios.ZOOM_ICON_SMALL_H, height, minimum=floor)}px; }}"
            " QLabel#topbarZoomIconLarge"
            f" {{ font-size: {px(layout_ratios.ZOOM_ICON_LARGE_H, height, minimum=floor)}px; }}"
        )
        if controls.styleSheet() != sheet:
            controls.setStyleSheet(sheet)

        # Gaps: inside the zoom cluster, and the spacer between it and the toggles.
        icon_gap = px(layout_ratios.ZOOM_ICON_GAP_H, height, minimum=0)
        controls_gap = px(layout_ratios.STATUS_CONTROLS_GAP_H, height, minimum=0)
        cluster = getattr(self, "topbar_zoom_cluster", None)
        if cluster is not None and cluster.layout() is not None:
            cluster.layout().setSpacing(icon_gap)
        layout = controls.layout()
        if layout is not None:
            layout.setSpacing(controls_gap)
            for index in range(layout.count()):
                spacer = layout.itemAt(index).spacerItem()
                if spacer is not None:
                    spacer.changeSize(controls_gap, 0)
            layout.invalidate()

        # The status bar's text labels: their widest allowed width.
        width = max(1, self._window.width())
        for name, ratio in (
            ("filter_summary_label", layout_ratios.STATUS_FILTER_W),
            ("catalog_status_label", layout_ratios.STATUS_CATALOG_W),
            ("cache_pipeline_label", layout_ratios.STATUS_PIPELINE_W),
        ):
            label = getattr(self._window, name, None)
            if label is not None:
                label.setMaximumWidth(px(ratio, width, minimum=80))

    def build_toolbar_strip(self, nav_cluster: QWidget, action_stack: QWidget) -> QFrame:
        """The customizable button bar as one movable unit.

        It shares the ``appTopBar`` object name so the top-bar button rules
        style it; ``toolbarPlacement`` picks the docked row or floating dock
        surface. ``_apply_toolbar_placement`` parents and positions it.
        """
        strip = QFrame()
        strip.setObjectName("appTopBar")
        strip.setProperty("toolbarPlacement", self._window._toolbar_placement)
        strip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(8, 4, 8, 4)
        strip_layout.setSpacing(WORKSPACE_METRICS.space_6)
        nav_cluster.setParent(strip)
        strip_layout.addWidget(nav_cluster, 0, Qt.AlignmentFlag.AlignVCenter)
        divider = QFrame(strip)
        divider.setObjectName("toolbarStripDivider")
        divider.setFixedSize(1, 30)
        strip_layout.addWidget(divider, 0, Qt.AlignmentFlag.AlignVCenter)
        action_stack.setParent(strip)
        strip_layout.addWidget(action_stack, 1)
        self._toolbar_strip_layout = strip_layout
        return strip

    def normalize_toolbar_placement(self, value: object) -> str:
        text = str(value or "").strip().lower()
        return text if text in self._window.TOOLBAR_PLACEMENTS else "floating"

    def set_toolbar_placement(self, placement: str) -> None:
        normalized = self.normalize_toolbar_placement(placement)
        if normalized == self._window._toolbar_placement:
            return
        self._window._toolbar_placement = normalized
        self._window._settings.setValue(self._window.TOOLBAR_PLACEMENT_KEY, normalized)
        self.apply_toolbar_placement()
        # Forced: a rare, user-initiated change that just reparented the strip
        # and swapped its surface, so fresh buttons are worth the few ms.
        self.rebuild_topbar_action_stack(force=True)
        profile = self.toolbar_profile()
        for button, _item_id in getattr(self, "_topbar_labeled_nav_buttons", ()):
            self._window._resize_topbar_button(button, profile)
        self._window._appearance.schedule_layout_ratio_update()
        self._window._update_action_states()

    def apply_toolbar_placement(self) -> None:
        strip = getattr(self, "toolbar_strip", None)
        center_layout = getattr(self._window, "workspace_center_layout", None)
        browser_stack = getattr(self._window, "browser_stack", None)
        if strip is None or center_layout is None or browser_stack is None:
            return
        floating = self._window._toolbar_placement == "floating"
        strip.setProperty("toolbarPlacement", self._window._toolbar_placement)
        center_layout.removeWidget(strip)
        controls = getattr(self, "view_controls", None)
        status = self._window.statusBar() if controls is not None else None
        if controls is not None and status is not None:
            status.removeWidget(controls)
            self._toolbar_strip_layout.removeWidget(controls)
            if floating:
                status.insertPermanentWidget(0, controls, 0)
            else:
                self._toolbar_strip_layout.addWidget(controls, 0, Qt.AlignmentFlag.AlignVCenter)
            controls.show()
            self.size_view_controls()
        if floating:
            strip.setParent(browser_stack.parentWidget())
            shadow = QGraphicsDropShadowEffect(strip)
            shadow.setBlurRadius(36)
            shadow.setOffset(0, 12)
            shadow.setColor(QColor(0, 0, 0, 150))
            strip.setGraphicsEffect(shadow)
            strip.show()
            self.position_floating_toolbar()
        else:
            strip.setGraphicsEffect(None)
            strip.setMinimumWidth(0)
            strip.setMaximumWidth(16777215)
            strip.setMinimumHeight(0)
            strip.setMaximumHeight(16777215)
            center_layout.insertWidget(0, strip)
            strip.show()
            self._window.grid.set_bottom_overlay(0, 0)
            self._window.details_view.set_bottom_overlay_reserve(0)
        strip.style().unpolish(strip)
        strip.style().polish(strip)
        strip.update()

    def floating_toolbar_width(self, available: int, *, expanded: bool = False) -> int:
        """The dock is a fixed share of the window width; while editing it
        opens to the full width so there is room to drop items."""
        if expanded:
            return max(0, available)
        return max(0, min(available, layout_ratios.ratio_px(layout_ratios.FLOATING_TOOLBAR_W, self._window.width())))

    def position_floating_toolbar(self, *, expanded: bool = False) -> None:
        strip = getattr(self, "toolbar_strip", None)
        browser_stack = getattr(self._window, "browser_stack", None)
        if strip is None or browser_stack is None or self._window._toolbar_placement != "floating":
            return
        area = browser_stack.geometry()
        px = layout_ratios.ratio_px
        floor = layout_ratios.MIN_GLYPH_PX
        margin = px(layout_ratios.FLOATING_TOOLBAR_BOTTOM_H, self._window.height(), minimum=floor)
        row_gap = px(layout_ratios.FLOATING_TOOLBAR_ROW_GAP_H, self._window.height(), minimum=0)
        fade = px(layout_ratios.FLOATING_TOOLBAR_FADE_H, self._window.height(), minimum=0)
        width = self.floating_toolbar_width(area.width() - 2 * self._window.FLOATING_TOOLBAR_SIDE_MARGIN, expanded=expanded)
        height = px(layout_ratios.FLOATING_TOOLBAR_H, self._window.height(), minimum=strip.minimumSizeHint().height())
        strip.setFixedWidth(width)
        strip.setFixedHeight(height)
        strip.setGeometry(area.x() + (area.width() - width) // 2, area.bottom() + 1 - margin - height, width, height)
        strip.raise_()
        reserve = height + margin + row_gap
        self._window.grid.set_bottom_overlay(reserve, reserve + fade)
        self._window.details_view.set_bottom_overlay_reserve(height + margin)

    def build_topbar_action_stack(self) -> QStackedWidget:
        """Mode-aware action cluster mirrored from the editable toolbar layout.

        Each mode page is rebuilt from ``_workspace_toolbar_layouts`` so the
        toolbar customizer drives the top bar (and the bar reflects the layout).
        Items that don't fit collapse into a trailing "More" overflow menu.
        """
        stack = QStackedWidget()
        stack.setObjectName("topbarActionStack")
        stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._topbar_action_layouts: dict[str, QGridLayout] = {}
        self._topbar_action_items: dict[str, list[tuple[str, QWidget]]] = {}
        self._topbar_slot_widgets: dict[str, list[QWidget | None]] = {}
        self._topbar_more_buttons: dict[str, QToolButton] = {}
        # The widgets the recorded key described no longer exist.
        self._topbar_rebuild_key: tuple | None = None
        for mode in ("manual", "ai"):
            page = QWidget()
            grid = QGridLayout(page)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(self._window.TOPBAR_SLOT_SPACING)
            grid.setVerticalSpacing(0)
            # Columns are configured during rebuild from the measured stack
            # width. Keeping all 35 columns active here creates a minimum width
            # wider than 1080p layouts can spare.
            for col in range(self._window.TOPBAR_SLOT_COUNT):
                grid.setColumnStretch(col, 0)
                grid.setColumnMinimumWidth(col, 0)
            self._topbar_action_layouts[mode] = grid
            stack.addWidget(page)
        # Assign early so the overflow pass can find the stack during the build.
        self._window.topbar_action_stack = stack
        stack.installEventFilter(self._window)
        # Nothing built yet, so there is nothing for the change guard to compare.
        self.rebuild_topbar_action_stack(force=True)
        stack.setCurrentIndex(0)
        return stack

    def topbar_popup_specs(self) -> dict[str, tuple[str, "Callable[[], QMenu]"]]:
        return {
            "review": ("Review", self._window._toolbar_menus.build_review_toolbar_menu),
            "view": ("View", self._window._toolbar_menus.build_view_toolbar_menu),
            "filters": ("Filters", self._window._toolbar_menus.build_quick_filter_toolbar_menu),
            "columns": ("Columns", self._window._toolbar_menus.build_columns_toolbar_menu),
            "sort": ("Sort", self._window._toolbar_menus.build_sort_toolbar_menu),
            "quick_filter": ("Quick Filter", self._window._toolbar_menus.build_quick_filter_toolbar_menu),
            "ai_results": ("AI Results", self._window._toolbar_menus.build_ai_results_menu),
            "projects": ("Collections", self._window._toolbar_menus.build_projects_toolbar_menu),
            "catalog": ("Library", self._window._toolbar_menus.build_catalog_toolbar_menu),
        }

    def build_topbar_action_item(self, item_id: str) -> QWidget | None:
        """Build a single top-bar centre widget for a layout item, or None when
        the item is top-bar chrome handled elsewhere (nav glyphs/search/path).

        Buttons carry the same Fluent toolbar icon used by the old workspace
        toolbar (``WORKSPACE_TOOLBAR_FLUENT_ICONS``) above a compact label.
        """
        if item_id in self._window.TOPBAR_CHROME_ITEMS:
            return None
        if item_id == "divider":
            return self.build_topbar_divider()
        theme = getattr(self._window, "_theme", None) or default_theme()
        icon_color = theme.text_primary.qcolor()
        icon = self._window._trim_icon_transparency(
            self.workspace_toolbar_icon(item_id, color=icon_color)
        )
        popup_specs = self.topbar_popup_specs()
        action: QAction | None = None
        if item_id in popup_specs:
            label, factory = popup_specs[item_id]
            menu = factory()
            button = self._window._toolbar_menus.build_popup_button(label, menu)
            # The factories parent every menu to the main window, so one would
            # outlive its button (with its submenus) on each rebuild. Hand it to
            # the button; keep its window flags, which a plain setParent resets.
            menu.setParent(button, menu.windowFlags())
        else:
            spec = self.workspace_toolbar_action_specs().get(item_id)
            if spec is None:
                return None
            action, text = spec
            button = QToolButton()
            button.setObjectName("appTopBarActionButton")
            button.setText(text)
            button.setToolTip(action.toolTip() or text)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.clicked.connect(
                lambda _checked=False, iid=item_id, src=action: self.activate_topbar_action(iid, src)
            )
            # Late-bound (not self._sync_topbar_action_button_for): the call resolves the method when it runs.
            _TopbarActionSync(lambda *args: self.sync_topbar_action_button_for(*args), button, action, item_id)
            self.sync_topbar_action_button_for(button, action, item_id)
        button.setText(self._window.TOPBAR_COMPACT_LABELS.get(item_id, button.text()))
        self.apply_topbar_button_style(button, icon)
        if action is not None:
            self.sync_topbar_action_button_for(button, action, item_id)
        button.setProperty("topbarItemId", item_id)
        return button

    def build_topbar_divider(self) -> QWidget:
        """An inert thin vertical pipe used purely as a visual group divider.
        No action, no click behaviour — it just occupies a cell."""
        holder = QWidget()
        holder.setObjectName("topbarDividerCell")
        profile = getattr(self._window, "_display_profile", None) or STANDARD_DISPLAY
        holder.setFixedSize(profile.topbar_slot_button_width, profile.topbar_button_height)
        layout = QHBoxLayout(holder)
        vertical_margin = max(5, round(profile.topbar_button_height * 0.21))
        layout.setContentsMargins(0, vertical_margin, 0, vertical_margin)
        layout.setSpacing(0)
        line = QFrame(holder)
        line.setObjectName("topbarDividerLine")
        line.setFixedWidth(2)
        line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(line, 0, Qt.AlignmentFlag.AlignHCenter)
        holder.setProperty("topbarItemId", "divider")
        return holder

    def item_target_mode_for_action(self, item_id: str) -> str | None:
        """The toolbar page an item belongs to, or None if it belongs to the one in use.
        Items from the retired AI page stay clickable on the Manual bar (``_sync_topbar_action_button_for``)."""
        current = "manual"
        if item_id in self._window.WORKSPACE_TOOLBAR_ALLOWED_ITEMS.get(current, ()):
            return None
        for mode in ("ai", "manual"):
            if item_id in self._window.WORKSPACE_TOOLBAR_ALLOWED_ITEMS.get(mode, ()):
                return mode
        return None

    def activate_topbar_action(self, item_id: str, action: QAction) -> None:
        action.trigger()

    def sync_topbar_action_button_for(self, button: QToolButton, action: QAction, item_id: str) -> None:
        # A button from the retired AI page, placed on the Manual bar, stays clickable even while its action is
        # disabled.
        cross = self.item_target_mode_for_action(item_id) is not None
        try:
            button.setEnabled(True if cross else action.isEnabled())
            button.setCheckable(action.isCheckable())
            if action.isCheckable():
                button.setChecked(action.isChecked())
            button.setToolTip(action.toolTip() or self._window.WORKSPACE_TOOLBAR_ITEM_LABELS.get(item_id, item_id))
            glyph = button.findChild(QToolButton, "appTopBarGlyph")
            if glyph is not None:
                glyph.setCheckable(action.isCheckable())
                glyph.setChecked(action.isChecked() if action.isCheckable() else False)
        except RuntimeError:
            pass

    def sync_topbar_action_buttons(self) -> None:
        """Re-read every action-backed top-bar button from its action.

        The buttons normally follow ``action.changed``. But the checked state of
        several actions (Compare, Auto-Advance, Smart Groups/Stacks, Hidden
        Folders, Zen) is pushed from window state under ``QSignalBlocker`` so a
        resync does not re-trigger the action, and that blocker swallows
        ``changed`` too: a button on the bar then keeps the old state until
        something happens to rebuild it. Call this after any such push.
        """
        built = getattr(self, "_topbar_action_items", None)
        if not built or getattr(self._window, "actions", None) is None:
            return
        specs = self.workspace_toolbar_action_specs()
        for items in built.values():
            for item_id, widget in items:
                spec = specs.get(item_id)
                if spec is not None and isinstance(widget, QToolButton):
                    self.sync_topbar_action_button_for(widget, spec[0], item_id)

    def apply_topbar_button_style(self, button: QToolButton, icon: QIcon) -> None:
        """Place every glyph and caption in identical fixed-height rows."""
        profile = self.toolbar_profile()
        caption = button.text()
        button.setMinimumSize(0, 0)
        button.setMaximumSize(16777215, 16777215)
        button.setObjectName("appTopBarIconButton")
        hover_width = profile.topbar_slot_button_width + 2 * profile.topbar_hover_margin
        hover_height = profile.topbar_button_height + 2 * profile.topbar_hover_margin
        button.setFixedSize(hover_width, hover_height)
        button.setAccessibleName(caption)
        button.setProperty("topbarCaption", caption)
        button.setText("")
        button.setIcon(QIcon())
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)

        content = QWidget(button)
        content.setObjectName("appTopBarButtonContent")
        content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        content.setGeometry(
            profile.topbar_hover_margin,
            profile.topbar_hover_margin,
            profile.topbar_slot_button_width,
            profile.topbar_button_height,
        )
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        glyph = QToolButton(content)
        glyph.setObjectName("appTopBarGlyph")
        glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        glyph.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        glyph.setIcon(icon)
        glyph.setIconSize(QSize(profile.topbar_glyph_size, profile.topbar_glyph_size))
        glyph.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        glyph.setFixedSize(profile.topbar_slot_button_width, profile.topbar_glyph_size)
        layout.addWidget(glyph, 0, Qt.AlignmentFlag.AlignHCenter)

        caption_label = QLabel(caption, content)
        caption_label.setObjectName("appTopBarButtonCaption")
        caption_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        caption_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        caption_label.setFixedSize(profile.topbar_slot_button_width, profile.topbar_caption_height)
        layout.addWidget(caption_label, 0, Qt.AlignmentFlag.AlignHCenter)

    def topbar_nav_icon(self, item_id: str) -> QIcon:
        glyphs = self._window.TOPBAR_NAV_FLUENT_ICONS.get(item_id)
        if glyphs is None:
            return QIcon()
        theme = getattr(self._window, "_theme", None) or default_theme()
        color = theme.text_primary.qcolor()
        primary, secondary = glyphs
        return self._window._trim_icon_transparency(
            self.fluent_toolbar_icon(primary, secondary, color=color)
        )

    def build_topbar_more_button(self) -> QToolButton:
        button = QToolButton()
        button.setObjectName("appTopBarActionButton")
        button.setText("More ▾")
        button.setToolTip("More toolbar buttons")
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(QMenu(button))
        # Honor the toolbar-style preference (text label vs the "more" glyph).
        self.apply_topbar_button_style(button, self.workspace_toolbar_icon("more"))
        return button

    def topbar_visible_slot_count_for_width(self, width: int | float) -> int:
        available = int(width or 0)
        if available <= 0:
            return max(1, min(self._window.TOPBAR_INITIAL_VISIBLE_SLOTS, self._window.TOPBAR_SLOT_COUNT))
        hover_width = self._window.TOPBAR_SLOT_BUTTON_WIDTH + 2 * self._window.TOPBAR_HOVER_MARGIN
        cell = max(self._window.TOPBAR_SLOT_CELL_MIN, hover_width)
        spacing = max(0, int(self._window.TOPBAR_SLOT_SPACING))
        count = (available + spacing) // (cell + spacing)
        return max(1, min(self._window.TOPBAR_SLOT_COUNT, int(count)))

    def topbar_visible_slot_count(self) -> int:
        stack = getattr(self._window, "topbar_action_stack", None)
        width = stack.width() if isinstance(stack, QWidget) else 0
        profile = self.toolbar_profile() if getattr(self._window, "_display_profile", None) is not None else None
        if profile is None:
            return self.topbar_visible_slot_count_for_width(width)
        available = int(width or 0)
        if available <= 0:
            return max(1, min(self._window.TOPBAR_INITIAL_VISIBLE_SLOTS, self._window.TOPBAR_SLOT_COUNT))
        hover_width = profile.topbar_slot_button_width + 2 * profile.topbar_hover_margin
        cell = max(profile.topbar_slot_cell_min, hover_width)
        spacing = max(0, profile.topbar_slot_spacing)
        count = (available + spacing) // (cell + spacing)
        if getattr(self._window, "_toolbar_placement", "docked") == "floating":
            # The dock is a fixed share of the window: spread the buttons in
            # use across it instead of leaving empty cells at the end.
            count = min(count, max(1, self.used_toolbar_slot_count()))
        return max(1, min(self._window.TOPBAR_SLOT_COUNT, int(count)))

    def configure_topbar_grid_columns(self, grid: QGridLayout, visible_slots: int) -> None:
        profile = self.toolbar_profile()
        for col in range(self._window.TOPBAR_SLOT_COUNT):
            active = col < visible_slots
            grid.setColumnStretch(col, 1 if active else 0)
            grid.setColumnMinimumWidth(col, profile.topbar_slot_cell_min if active else 0)

    def topbar_rebuild_inputs(self, visible_slots: int) -> tuple:
        """Everything ``_rebuild_topbar_action_stack`` reads that can change what
        it builds, as a value to compare between calls.

        Live action state (enabled, checked, tooltip) is deliberately absent:
        the buttons follow it themselves (``_TopbarActionSync`` and
        ``_sync_topbar_action_buttons``). The popup menus are built from shared
        actions, so they follow it too. The icon cache is also absent; whoever
        clears it passes ``force=True``.
        """
        slots = getattr(self._window, "_topbar_slots", None) or {}
        return (
            visible_slots,
            tuple(slots.get("manual") or ()),
            tuple(slots.get("ai") or ()),
            # Every button metric (and, floating, the scale derived from the window size).
            self.toolbar_profile(),
            # Icon colours.
            getattr(self._window, "_theme", None),
            getattr(self._window, "_toolbar_placement", "docked"),
        )

    def topbar_stack_intact(self) -> bool:
        """True while every widget the last rebuild made is still alive and in its grid."""
        layouts = getattr(self, "_topbar_action_layouts", None) or {}
        built = getattr(self, "_topbar_action_items", None) or {}
        try:
            for target in ("manual", "ai"):
                grid = layouts.get(target)
                items = built.get(target)
                if grid is None or items is None or grid.count() != len(items):
                    return False
                if any(grid.indexOf(widget) < 0 for _item_id, widget in items):
                    return False
        except RuntimeError:  # a wrapped C++ widget was deleted underneath us
            return False
        return True

    def rebuild_topbar_action_stack(self, mode: str | None = None, *, force: bool = False) -> None:
        """Rebuild both top-bar pages from the shared slots.

        Cheap to call often: when none of ``_topbar_rebuild_inputs`` changed since
        the last rebuild and its widgets are all still alive, nothing is built
        (about half of the calls made over a launch and a resize were identical
        rebuilds). ``force=True`` rebuilds regardless; pass it when something the
        inputs do not capture has changed, such as a cleared icon cache.
        Always synchronous: deferring the layout on resize drew a visible jump.
        """
        layouts = getattr(self, "_topbar_action_layouts", None)
        if not layouts:
            return
        # Unified bar: both mode pages render the same shared slots, so always
        # rebuild both regardless of the requested mode.
        modes = ("manual", "ai")
        n = self._window.TOPBAR_SLOT_COUNT
        visible_slots = self.topbar_visible_slot_count()
        rebuild_key = self.topbar_rebuild_inputs(visible_slots)
        if (
            not force
            and rebuild_key == getattr(self, "_topbar_rebuild_key", None)
            and self.topbar_stack_intact()
        ):
            return
        # Recorded again only once the build below has completed, so a build that
        # raises part-way can never be mistaken for a finished one.
        self._topbar_rebuild_key = None
        self._topbar_rendered_slot_count = visible_slots
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        built = 0
        for target in modes:
            grid = layouts.get(target)
            if grid is None:
                continue
            self.clear_layout_items(grid, delete_widgets=True)
            self.configure_topbar_grid_columns(grid, visible_slots)
            slots = list(getattr(self._window, "_topbar_slots", {}).get(target) or [])
            slots += [None] * (n - len(slots))
            hidden_start = visible_slots
            if any(item_id for item_id in slots[visible_slots:]):
                hidden_start = max(0, visible_slots - 1)
            items: list[tuple[str, QWidget]] = []
            slot_widgets: list[QWidget | None] = [None] * n
            for slot_index in range(hidden_start):
                item_id = slots[slot_index]
                if not item_id:
                    continue
                widget = self.build_topbar_action_item(item_id)
                if widget is None:
                    continue
                self.normalize_topbar_slot_button(widget)
                grid.addWidget(widget, 0, slot_index, Qt.AlignmentFlag.AlignCenter)
                items.append((item_id, widget))
                slot_widgets[slot_index] = widget
                built += 1
            hidden_items = [item_id for item_id in slots[hidden_start:] if item_id]
            if hidden_items and visible_slots > 0:
                button = self.build_topbar_more_button()
                menu = QMenu(button)
                for item_id in hidden_items:
                    self.add_topbar_overflow_entry(menu, item_id)
                button.setMenu(menu)
                self.normalize_topbar_slot_button(button)
                more_slot = visible_slots - 1
                grid.addWidget(button, 0, more_slot, Qt.AlignmentFlag.AlignCenter)
                items.append(("more", button))
                slot_widgets[more_slot] = button
                self._topbar_more_buttons[target] = button
                built += 1
            else:
                self._topbar_more_buttons.pop(target, None)
            self._topbar_action_items[target] = items
            self._topbar_slot_widgets[target] = slot_widgets
        self._topbar_rebuild_key = rebuild_key
        if logger.enabled:
            logger.duration("toolbar.rebuild_stack", (time.perf_counter() - start) * 1000.0, widgets=built)
        if getattr(self._window, "_toolbar_placement", "docked") == "floating" and getattr(self, "toolbar_strip", None) is not None:
            QTimer.singleShot(0, self.position_floating_toolbar)

    def normalize_topbar_slot_button(self, widget: QWidget) -> None:
        # One uniform width so every cell reads the same regardless of whether
        # the button shows an icon or text (text elides at this width).
        if isinstance(widget, QToolButton):
            profile = self.toolbar_profile()
            if widget.findChild(QWidget, "appTopBarButtonContent") is not None:
                self._window._resize_topbar_button(widget, profile)
            else:
                widget.setFixedWidth(profile.topbar_slot_button_width + 2 * profile.topbar_hover_margin)

    def add_topbar_overflow_entry(self, menu: QMenu, item_id: str) -> None:
        popup_specs = self.topbar_popup_specs()
        if item_id in popup_specs:
            label, factory = popup_specs[item_id]
            self.add_topbar_overflow_popup_entries(menu, label, factory)
            return
        spec = self.workspace_toolbar_action_specs().get(item_id)
        if spec is not None:
            menu.addAction(spec[0])

    def add_topbar_overflow_popup_entries(self, menu: QMenu, label: str, factory: "Callable[[], QMenu]") -> None:
        source_menu = factory()
        actions = list(source_menu.actions())
        if not actions:
            source_menu.deleteLater()
            return
        if menu.actions():
            menu.addSeparator()
        menu.addSection(label)
        for action in actions:
            submenu = action.menu()
            if submenu is not None:
                submenu.setParent(menu)
                menu.addMenu(submenu)
            elif action.isSeparator():
                menu.addSeparator()
            else:
                menu.addAction(action)
        self._window._keep_topbar_overflow_menu_source(menu, source_menu)

    def update_topbar_overflow(self, mode: str) -> None:
        visible_slots = self.topbar_visible_slot_count()
        if visible_slots == getattr(self, "_topbar_rendered_slot_count", None):
            return
        # Forced: the check above is already the change detection (the visible
        # slot count moved), and this runs synchronously from the stack's resize.
        self.rebuild_topbar_action_stack(mode, force=True)

    def workspace_toolbar_icon(self, item_id: str, *, color: QColor | None = None) -> QIcon:
        glyphs = self._window.WORKSPACE_TOOLBAR_FLUENT_ICONS.get(item_id)
        if glyphs is None:
            return QIcon()
        primary, secondary = glyphs
        return self.fluent_toolbar_icon(primary, secondary, color=color)

    def fluent_toolbar_icon(
        self,
        primary: str,
        secondary: str | None = None,
        *,
        color: QColor | None = None,
        primary_size: int = 31,
    ) -> QIcon:
        theme = getattr(self._window, "_theme", None) or default_theme()
        if color is None:
            color = theme.text_secondary.qcolor()
        accent = theme.accent.qcolor()
        active = theme.text_primary.qcolor()
        active_accent = theme.accent_hover.qcolor()
        selected = theme.accent.qcolor()
        disabled = theme.text_muted.qcolor() if not theme.is_dark else theme.text_disabled.qcolor()
        # The rendered glyph depends only on (primary, secondary, color, accent) —
        # never on the owning action's enabled/checked state (Qt auto-dims the
        # disabled variant). Memoize so the per-action.changed toolbar syncs are
        # cache hits instead of re-rasterizing a 64x64 pixmap each time, which
        # otherwise stalls folder loads by seconds when the icon toolbar style
        # is active. The colour key makes the cache self-invalidate on theme change.
        cache = self._window.__dict__.setdefault("_fluent_toolbar_icon_cache", {})
        cache_key = (
            primary,
            secondary,
            color.rgba(),
            accent.rgba(),
            active.rgba(),
            active_accent.rgba(),
            selected.rgba(),
            disabled.rgba(),
            primary_size,
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        def render(primary_color: QColor, secondary_color: QColor) -> QPixmap:
            return self._window._render_fluent_glyphs(
                primary, secondary, primary_color, secondary_color, primary_size=primary_size
            )

        pixmap = render(color, accent)
        icon = QIcon(pixmap)
        icon.addPixmap(render(active, active_accent), QIcon.Mode.Active, QIcon.State.Off)
        icon.addPixmap(render(selected, active_accent), QIcon.Mode.Normal, QIcon.State.On)
        icon.addPixmap(render(active_accent, selected), QIcon.Mode.Active, QIcon.State.On)
        icon.addPixmap(render(disabled, disabled), QIcon.Mode.Disabled)
        cache[cache_key] = icon
        return icon

    def configure_workspace_toolbar_button(self, button: QToolButton, *, item_id: str, text: str) -> None:
        style = self._window._normalize_toolbar_style(getattr(self._window, "_toolbar_style", "text"))
        icon = self.workspace_toolbar_icon(item_id)
        action = button.defaultAction()
        button.setToolTip(button.toolTip() or text)
        button.setProperty("toolbarItemId", item_id)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if style == "text":
            button.setObjectName("workspacePresetsButton")
            button.setText(text)
            button.setIcon(QIcon())
            if action is not None:
                action.setIcon(QIcon())
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setAutoRaise(False)
            button.setMinimumSize(0, 0)
            button.setMaximumSize(16777215, 16777215)
            return
        icon_size = 32 if style == "large_icons" else 22
        button.setObjectName("workspaceIconButton")
        if action is not None:
            action.setIcon(icon)
        button.setIcon(icon)
        button.setText("")
        button.setIconSize(QSize(icon_size, icon_size))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAutoRaise(True)
        side = 42 if style == "large_icons" else 32
        button.setFixedSize(side, side)

    def build_workspace_bar_button(self, text: str, tooltip: str, *, object_name: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName(object_name)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setFixedSize(24, 24)
        return button

    def build_left_rail_pinned_tools(self) -> None:
        """Pinned tools under the rail destinations, with a + at the rail's
        foot that pins any toolbar command. Right-click a pin to unpin it."""
        section = QWidget()
        section.setObjectName("leftRailPinned")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(4)
        divider = QFrame(section)
        divider.setObjectName("leftRailDivider")
        divider.setFixedSize(44, 1)
        layout.addWidget(divider, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(4)
        label = QLabel("PINNED", section)
        label.setObjectName("leftRailSectionLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)
        self._left_rail_divider = divider
        self._left_rail_section_label = label
        self._left_rail_section_layout = layout
        self._left_rail_pinned_layout = QVBoxLayout()
        self._left_rail_pinned_layout.setContentsMargins(0, 0, 0, 0)
        self._left_rail_pinned_layout.setSpacing(4)
        layout.addLayout(self._left_rail_pinned_layout)
        self.left_rail_pinned = section
        self._window.left_nav_rail.add_section(section)

        add_button = QToolButton()
        add_button.setObjectName("leftRailAddButton")
        add_button.setToolTip("Pin a tool")
        add_button.setAutoRaise(True)
        add_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        add_button.setFixedSize(40, 40)
        add_button.setIconSize(QSize(24, 24))
        add_button.clicked.connect(lambda _checked=False: self.show_pin_tool_menu(add_button))
        self.left_rail_add_button = add_button
        self._window.left_nav_rail.set_footer(add_button)
        self.rebuild_pinned_tools()
        self._window._appearance.apply_left_rail_metrics()

    def pinned_tool_ids(self) -> list[str]:
        raw = self._window._settings.value(self._window.PINNED_TOOLS_KEY, None)
        if raw is None:
            return list(self._window.DEFAULT_PINNED_TOOLS)
        try:
            values = json.loads(raw) if isinstance(raw, str) else list(raw)
        except (TypeError, ValueError):
            return list(self._window.DEFAULT_PINNED_TOOLS)
        return [str(value) for value in values if isinstance(value, str) and value]

    def set_pinned_tool_ids(self, ids: list[str]) -> None:
        self._window._settings.setValue(self._window.PINNED_TOOLS_KEY, json.dumps(ids))
        self.rebuild_pinned_tools()

    def rebuild_pinned_tools(self) -> None:
        layout = getattr(self, "_left_rail_pinned_layout", None)
        if layout is None:
            return
        self.clear_layout_items(layout, delete_widgets=True)
        specs = self.workspace_toolbar_action_specs()
        pinned = [item_id for item_id in self.pinned_tool_ids() if item_id in specs]
        self._left_rail_tool_buttons = []
        for item_id in pinned:
            action, label = specs[item_id]
            button = QToolButton()
            button.setObjectName("leftRailToolButton")
            # The icon and its size are set by _apply_left_rail_metrics below,
            # which knows the rail's width and whether artwork is installed.
            button.setAutoRaise(True)
            self._left_rail_tool_buttons.append((button, item_id))
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setToolTip(action.toolTip() or label)
            button.setAccessibleName(label)
            button.clicked.connect(lambda _checked=False, target=action: target.trigger())
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda _pos, target=item_id, anchor=button: self.show_pinned_tool_context_menu(target, anchor)
            )
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.left_rail_pinned.setVisible(bool(pinned))
        self.left_rail_add_button.setIcon(
            self.fluent_toolbar_icon("E710", color=self._window._appearance.chrome_icon_color())
        )
        self._window._appearance.apply_left_rail_metrics()

    def show_pinned_tool_context_menu(self, item_id: str, anchor: QWidget) -> None:
        if self._window._collection_mode:
            return
        menu = QMenu(self._window)
        unpin = menu.addAction("Unpin")
        chosen = menu.exec(anchor.mapToGlobal(QPoint(anchor.width(), 0)))
        if chosen is unpin:
            self.set_pinned_tool_ids([value for value in self.pinned_tool_ids() if value != item_id])

    def show_pin_tool_menu(self, anchor: QWidget) -> None:
        if self._window._collection_mode:
            return
        menu = QMenu(self._window)
        menu.setToolTipsVisible(True)
        pinned = self.pinned_tool_ids()
        specs = self.workspace_toolbar_action_specs()
        for item_id, (action, label) in sorted(specs.items(), key=lambda entry: entry[1][1].casefold()):
            entry = menu.addAction(self.workspace_toolbar_icon(item_id, color=self._window._appearance.chrome_icon_color()), label)
            entry.setCheckable(True)
            entry.setChecked(item_id in pinned)
            entry.setToolTip(action.toolTip())
            entry.setData(item_id)
        chosen = menu.exec(anchor.mapToGlobal(QPoint(anchor.width(), 0)))
        if chosen is None:
            return
        item_id = str(chosen.data())
        if item_id in pinned:
            pinned.remove(item_id)
        else:
            pinned.append(item_id)
        self.set_pinned_tool_ids(pinned)

    def build_left_rail_plus_button(
        self, parent: QWidget | None = None, *, tooltip: str
    ) -> QToolButton:
        button = QToolButton(parent)
        button.setObjectName("generatedLeftRailButton")
        button.setProperty("fluentGlyph", "E710")
        button.setIcon(self.fluent_toolbar_icon("E710", color=self._window._appearance.chrome_icon_color()))
        button.setIconSize(QSize(22, 22))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setFixedSize(30, 30)
        return button

    def build_workspace_toolbar_overflow_button(self, mode: str) -> QToolButton:
        menu = QMenu(self._window)
        menu.aboutToShow.connect(lambda target=mode: self.populate_workspace_toolbar_overflow_menu(target))
        button = QToolButton()
        button.setText("More")
        button.setToolTip("Hidden toolbar items")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(menu)
        self.configure_workspace_toolbar_button(button, item_id="more", text="More")
        button.hide()
        self._workspace_toolbar_overflow_menus[mode] = menu
        return button

    def build_workspace_action_button(self, action: QAction, text: str, *, item_id: str) -> QToolButton:
        button = QToolButton()
        button.setToolTip(action.toolTip() or text)
        if self._window._normalize_toolbar_style(getattr(self._window, "_toolbar_style", "text")) == "text":
            button.setDefaultAction(action)
        else:
            button.setProperty("workspaceAction", action)
            button.clicked.connect(lambda _checked=False, source=action: source.trigger())
            action.changed.connect(lambda target=button, source=action, target_item=item_id, label=text: self.sync_workspace_action_button(target, source, target_item, label))
        self.configure_workspace_toolbar_button(button, item_id=item_id, text=text)
        if button.defaultAction() is None:
            self.sync_workspace_action_button(button, action, item_id, text)
        return button

    def sync_workspace_action_button(self, button: QToolButton, action: QAction, item_id: str, text: str) -> None:
        try:
            if self._window._normalize_toolbar_style(getattr(self._window, "_toolbar_style", "text")) == "text":
                return
            button.setEnabled(action.isEnabled())
            button.setCheckable(action.isCheckable())
            button.setChecked(action.isChecked())
            button.setToolTip(action.toolTip() or text)
            self.configure_workspace_toolbar_button(button, item_id=item_id, text=text)
        except RuntimeError:
            return

    def workspace_toolbar_action_specs(self) -> dict[str, tuple[QAction, str]]:
        """Shared item_id -> (action, label) map used by both the workspace
        toolbar widgets and the mirrored top-bar action cluster."""
        return {
            "open_folder": (self._window.actions.open_folder, "Open"),
            "refresh_folder": (self._window.actions.refresh_folder, "Refresh"),
            "undo": (self._window.actions.undo, "Undo"),
            "new_folder": (self._window.actions.new_folder, "New Folder"),
            "open_preview": (self._window.actions.open_preview, "Preview"),
            "rename_selection": (self._window.actions.rename_selection, "Rename"),
            "move_selection_to_new_folder": (self._window.actions.move_selection_to_new_folder, "Move New"),
            "restore_selection": (self._window.actions.restore_selection, "Restore"),
            "zen_mode": (self._window.actions.zen_mode, "Zen"),
            "winner_ladder_mode": (self._window.actions.winner_ladder_mode, "Ladder"),
            "run_ai_culling": (self._window.actions.run_ai_culling, "Run Review"),
            "quick_rerank_ai_culling": (self._window.actions.quick_rerank_ai_culling, "Rerank"),
            "apply_ai_culling": (self._window.actions.apply_ai_culling, "Apply Cull"),
            "sort_ai_semantic_folders": (self._window.actions.sort_ai_semantic_folders, "Semantic Sort"),
            "reset_ai_review_cache": (self._window.actions.reset_ai_review_cache, "Reset AI"),
            "command_palette": (self._window.actions.open_command_palette, "Command"),
            "advanced_filters": (self._window.actions.advanced_filters, "Adv. Filters"),
            "clear_filters": (self._window.actions.clear_filters, "Clear"),
            "batch_rename": (self._window.actions.batch_rename_selection, "Rename"),
            "batch_resize": (self._window.actions.batch_resize_selection, "Resize"),
            "batch_convert": (self._window.actions.batch_convert_selection, "Convert"),
            "share_to_phone": (self._window.actions.share_to_phone, "PocketDrop"),
            "handoff_builder": (self._window.actions.handoff_builder, "Handoff"),
            "send_to_editor": (self._window.actions.send_to_editor_pipeline, "Editor"),
            "best_of_set": (self._window.actions.best_of_set_auto_assembly, "Best Of"),
            "keyboard_shortcuts": (self._window.actions.keyboard_shortcuts, "Shortcuts"),
            "compare": (self._window.actions.compare_mode, "Compare"),
            "auto_advance": (self._window.actions.auto_advance, "Auto"),
            "burst_groups": (self._window.actions.burst_groups, "Groups"),
            "burst_stacks": (self._window.actions.burst_stacks, "Stacks"),
            "show_hidden_folders": (self._window.actions.show_hidden_folders, "Hidden"),
            "accept_selection": (self._window.actions.accept_selection, "Winner"),
            "reject_selection": (self._window.actions.reject_selection, "Reject"),
            "keep_selection": (self._window.actions.keep_selection, "Keep"),
            "move_selection": (self._window.actions.move_selection, "Move"),
            "delete_selection": (self._window.actions.delete_selection, "Delete"),
            "reveal_in_explorer": (self._window.actions.reveal_in_explorer, "Reveal"),
            "open_in_photoshop": (self._window.actions.open_in_photoshop, "Photoshop"),
            "load_saved_ai": (self._window.actions.load_saved_ai, "Load Saved"),
            "load_ai_results": (self._window.actions.load_ai_results, "Load AI"),
            "clear_ai_results": (self._window.actions.clear_ai_results, "Clear AI"),
            "open_ai_report": (self._window.actions.open_ai_report, "Report"),
            "manage_people": (self._window.actions.manage_people, "People"),
            "show_ai_review_summary": (self._window.actions.show_ai_review_summary, "Summary"),
            "next_ai_pick": (self._window.actions.next_ai_pick, "Next Pick"),
            "next_unreviewed_ai_pick": (self._window.actions.next_unreviewed_ai_pick, "Next Unreviewed"),
            "compare_ai_group": (self._window.actions.compare_ai_group, "AI Compare"),
            "review_ai_disagreements": (self._window.actions.review_ai_disagreements, "Disagree"),
            "save_filter_preset": (self._window.actions.save_filter_preset, "Save Search"),
        }

    def build_workspace_toolbar_widgets(self, mode: str) -> dict[str, QWidget]:
        if mode == "ai":
            widgets: dict[str, QWidget] = {
                "ai_status": self._window.ai_status_widget,
                "review": self._window.ai_review_tools_button,
                "view": self._window.ai_view_tools_button,
                "search": self._window.ai_search_field,
                "filters": self._window.ai_filter_button,
                "address": self._window.ai_path_control,
                "selection_count": self._window.ai_selection_count_label,
            }
        else:
            widgets = {
                "review": self._window.manual_review_tools_button,
                "view": self._window.manual_view_tools_button,
                "search": self._window.manual_search_field,
                "filters": self._window.manual_filter_button,
                "address": self._window.manual_path_control,
                "selection_count": self._window.manual_selection_count_label,
            }
        for item_id in ("review", "view", "filters"):
            widget = widgets.get(item_id)
            if isinstance(widget, QToolButton):
                self.configure_workspace_toolbar_button(
                    widget,
                    item_id=item_id,
                    text=self._window.WORKSPACE_TOOLBAR_ITEM_LABELS.get(item_id, item_id),
                )

        menu_factories = {
            "columns": ("Columns", self._window._toolbar_menus.build_columns_toolbar_menu),
            "sort": ("Sort", self._window._toolbar_menus.build_sort_toolbar_menu),
            "quick_filter": ("Quick Filter", self._window._toolbar_menus.build_quick_filter_toolbar_menu),
            "ai_results": ("AI Results", self._window._toolbar_menus.build_ai_results_menu),
            "projects": ("Collections", self._window._toolbar_menus.build_projects_toolbar_menu),
            "catalog": ("Library", self._window._toolbar_menus.build_catalog_toolbar_menu),
        }
        for item_id, (text, factory) in menu_factories.items():
            button = self._window._toolbar_menus.build_popup_button(text, factory())
            self.configure_workspace_toolbar_button(button, item_id=item_id, text=text)
            widgets[item_id] = button

        action_items = self.workspace_toolbar_action_specs()
        for item_id, (action, text) in action_items.items():
            widgets[item_id] = self.build_workspace_action_button(action, text, item_id=item_id)
        return widgets

    def load_workspace_toolbar_layouts(self) -> dict[str, list[str]]:
        layouts = {mode: list(items) for mode, items in self._window.WORKSPACE_TOOLBAR_DEFAULTS.items()}
        raw_state = self._window._settings.value(self._window.WORKSPACE_TOOLBAR_LAYOUT_KEY, "", str)
        if not isinstance(raw_state, str) or not raw_state:
            self.ensure_workspace_toolbar_migrations(layouts)
            return layouts
        try:
            payload = json.loads(raw_state)
        except (TypeError, ValueError):
            return layouts
        if not isinstance(payload, dict):
            return layouts
        raw_layouts = payload.get("toolbars", payload)
        if not isinstance(raw_layouts, dict):
            return layouts

        legacy_primary_items: list[str] = []
        raw_primary_items = raw_layouts.get("primary")
        if isinstance(raw_primary_items, list):
            legacy_primary_items = self.normalize_legacy_primary_toolbar_items(raw_primary_items)
        for mode in self._window.WORKSPACE_TOOLBAR_DEFAULTS:
            raw_items = raw_layouts.get(mode)
            if isinstance(raw_items, list):
                layouts[mode] = self.normalize_workspace_toolbar_items(mode, raw_items)
        if legacy_primary_items:
            self.merge_legacy_primary_toolbar_items(layouts, legacy_primary_items)
        self.ensure_workspace_toolbar_migrations(layouts)
        return layouts

    def ensure_workspace_toolbar_migrations(self, layouts: dict[str, list[str]]) -> None:
        ai_items = list(layouts.get("ai", ()))
        ai_items = [item for item in ai_items if item != "ai_status"]
        ai_items.insert(0, "ai_status")
        if "apply_ai_culling" not in ai_items:
            if "run_ai_culling" in ai_items:
                insert_at = ai_items.index("run_ai_culling") + 1
                ai_items.insert(insert_at, "apply_ai_culling")
            else:
                ai_items.insert(0, "apply_ai_culling")
        if "reset_ai_review_cache" not in ai_items:
            if "apply_ai_culling" in ai_items:
                insert_at = ai_items.index("apply_ai_culling") + 1
                ai_items.insert(insert_at, "reset_ai_review_cache")
            elif "run_ai_culling" in ai_items:
                insert_at = ai_items.index("run_ai_culling") + 1
                ai_items.insert(insert_at, "reset_ai_review_cache")
            else:
                ai_items.insert(0, "reset_ai_review_cache")
        if "sort_ai_semantic_folders" not in ai_items:
            if "apply_ai_culling" in ai_items:
                insert_at = ai_items.index("apply_ai_culling") + 1
                ai_items.insert(insert_at, "sort_ai_semantic_folders")
            elif "run_ai_culling" in ai_items:
                insert_at = ai_items.index("run_ai_culling") + 1
                ai_items.insert(insert_at, "sort_ai_semantic_folders")
            else:
                ai_items.insert(0, "sort_ai_semantic_folders")
        layouts["ai"] = self.normalize_workspace_toolbar_items("ai", ai_items)

    def normalize_workspace_toolbar_items(self, mode: str, raw_items: list[object] | tuple[object, ...]) -> list[str]:
        allowed = set(self._window.WORKSPACE_TOOLBAR_ALLOWED_ITEMS.get(mode, ()))
        normalized: list[str] = []
        for item in raw_items:
            if not isinstance(item, str) or item not in allowed or item in normalized:
                continue
            normalized.append(item)
        return normalized

    def normalize_legacy_primary_toolbar_items(self, raw_items: list[object] | tuple[object, ...]) -> list[str]:
        allowed = set(self._window.LEGACY_PRIMARY_TOOLBAR_ITEMS)
        normalized: list[str] = []
        for item in raw_items:
            if not isinstance(item, str) or item not in allowed or item in normalized:
                continue
            normalized.append(item)
        return normalized

    def merge_legacy_primary_toolbar_items(self, layouts: dict[str, list[str]], legacy_items: list[str]) -> None:
        migrated: dict[str, list[str]] = {"manual": [], "ai": []}
        for item in legacy_items:
            if item == "separator":
                continue
            target_mode = "ai" if item in {"run_ai_culling", "ai_results"} else "manual"
            allowed = self._window.WORKSPACE_TOOLBAR_ALLOWED_ITEMS.get(target_mode, ())
            if item not in allowed or item in migrated[target_mode]:
                continue
            migrated[target_mode].append(item)

        for mode, migrated_items in migrated.items():
            if not migrated_items:
                continue
            existing = layouts.get(mode, [])
            layouts[mode] = migrated_items + [item for item in existing if item not in migrated_items]

    def unified_allowed_items(self) -> set[str]:
        # Buttons can live on either bar ("cross-contamination"), so a slot may
        # hold any cluster item allowed in *either* mode. Structural (no actions).
        result: set[str] = set(self._window.TOPBAR_REPEATABLE_ITEMS)
        for mode in self._window.WORKSPACE_TOOLBAR_DEFAULTS:
            result |= set(self._window.WORKSPACE_TOOLBAR_ALLOWED_ITEMS.get(mode, ()))
        return result

    def slots_to_items(self, slots) -> list[str]:
        return [value for value in slots if value]

    def normalize_slots(self, mode: str, raw) -> list[str | None]:
        n = self._window.TOPBAR_SLOT_COUNT
        usable = n
        allowed = self.unified_allowed_items()
        result: list[str | None] = [None] * n
        seen: set[str] = set()
        for idx, value in enumerate(list(raw)[:usable]):
            if not self._window._is_cluster_item(value) or value not in allowed:
                continue
            if value not in self._window.TOPBAR_REPEATABLE_ITEMS:
                if value in seen:
                    continue
                seen.add(value)
            result[idx] = value
        return result

    def load_topbar_slots(self) -> dict[str, list[str | None]]:
        payload: dict = {}
        raw_state = self._window._settings.value(self._window.WORKSPACE_TOOLBAR_LAYOUT_KEY, "", str)
        if isinstance(raw_state, str) and raw_state:
            try:
                loaded = json.loads(raw_state)
                if isinstance(loaded, dict):
                    payload = loaded
            except (TypeError, ValueError):
                payload = {}
        raw_slots = payload.get("slots") if isinstance(payload, dict) else None
        per_mode: dict[str, list[str | None]] = {}
        for mode in self._window.WORKSPACE_TOOLBAR_DEFAULTS:
            mode_slots: list[str | None] | None = None
            if isinstance(raw_slots, dict) and isinstance(raw_slots.get(mode), list):
                mode_slots = self.normalize_slots(mode, raw_slots[mode])
            if mode_slots is None:
                mode_slots = self._window._items_to_slots(self._window._workspace_toolbar_layouts.get(mode, ()))
            per_mode[mode] = mode_slots
        # Unified bar: both review modes share one arrangement. Only MERGE when the
        # persisted arrays actually differ (a one-time migration from the old
        # separate bars). When they're already identical — the normal case — reuse
        # one directly: re-merging identical arrays would multiply repeatable items
        # (dividers) on every launch (1 -> 3 -> 9 ...).
        mode_keys = list(self._window.WORKSPACE_TOOLBAR_DEFAULTS)
        first = per_mode.get(mode_keys[0]) if mode_keys else None
        if first is not None and all(per_mode.get(m) == first for m in mode_keys):
            shared = list(first)
        else:
            shared = self.merge_slots_shared(per_mode)
        slots: dict[str, list[str | None]] = {}
        for mode in self._window.WORKSPACE_TOOLBAR_DEFAULTS:
            slots[mode] = list(shared)
            self.sync_items_from_slots(mode, slots_override=shared)
        return slots

    def merge_slots_shared(self, per_mode: dict[str, list[str | None]]) -> list[str | None]:
        """Fold per-mode slot layouts into one shared layout: items keep their
        cell when it's free (manual wins ties), and anything displaced falls into
        the next open cell."""
        n = self._window.TOPBAR_SLOT_COUNT
        usable = n
        shared: list[str | None] = [None] * n
        seen: set[str] = set()

        def mark(item: str) -> None:
            if item not in self._window.TOPBAR_REPEATABLE_ITEMS:
                seen.add(item)

        for mode in ("manual", "ai"):
            for idx, item in enumerate(per_mode.get(mode, [])):
                if not item or item in seen or idx >= usable or shared[idx] is not None:
                    continue
                shared[idx] = item
                mark(item)
        for mode in ("manual", "ai"):
            for item in per_mode.get(mode, []):
                if not item or item in seen:
                    continue
                target = next((i for i in range(usable) if shared[i] is None), None)
                if target is None:
                    break
                shared[target] = item
                mark(item)
        return shared

    def apply_cluster_order(self, mode: str, cluster_order: list[str]) -> None:
        """Write a new ordering of cluster items back into the flat layout list,
        leaving chrome items (search/path/etc.) in their existing relative
        positions. Cluster items beyond the new order are dropped; extras are
        appended (the flat list only drives the hidden legacy bar)."""
        queue = list(cluster_order)
        merged: list[str] = []
        for item in self._window._workspace_toolbar_layouts.get(mode, ()):
            if self._window._is_cluster_item(item):
                if queue:
                    merged.append(queue.pop(0))
            else:
                merged.append(item)
        merged.extend(queue)
        self._window._workspace_toolbar_layouts[mode] = merged

    def sync_items_from_slots(self, mode: str, slots_override: list[str | None] | None = None) -> None:
        slots = slots_override if slots_override is not None else getattr(self._window, "_topbar_slots", {}).get(mode, [])
        self.apply_cluster_order(mode, self.slots_to_items(slots))

    def clear_layout_items(self, layout, *, delete_widgets: bool = False) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                child_layout = item.layout()
                if child_layout is not None:
                    self.clear_layout_items(child_layout, delete_widgets=delete_widgets)
                continue
            if delete_widgets:
                widget.deleteLater()
            else:
                widget.setParent(None)

    def rebuild_workspace_toolbar(self, mode: str) -> None:
        if mode == "ai":
            layout = self._window.ai_toolbar_layout
        else:
            layout = self._window.manual_toolbar_layout
            mode = "manual"
        self.clear_layout_items(layout)
        widgets = self._window._workspace_toolbar_item_widgets.get(mode, {})
        address_widget: QWidget | None = None
        has_search = False
        for item_id in self._window._workspace_toolbar_layouts.get(mode, []):
            widget = widgets.get(item_id)
            if widget is None:
                continue
            if item_id == "address":
                address_widget = widget
                continue
            is_search = item_id == "search"
            has_search = has_search or is_search
            layout.addWidget(widget, 1 if is_search else 0)
        overflow_button = self._window._workspace_toolbar_overflow_buttons.get(mode)
        if overflow_button is not None:
            layout.addWidget(overflow_button, 0, Qt.AlignmentFlag.AlignRight)
        if address_widget is not None:
            if not has_search:
                layout.addStretch(1)
            layout.addWidget(address_widget, 0, Qt.AlignmentFlag.AlignRight)
        elif not has_search:
            layout.addStretch(1)
        self.schedule_workspace_toolbar_overflow_update(mode)
        # Mirror the same editable layout into the top-bar action cluster.
        # Forced: this is the "the layout was just edited" path, whose inputs
        # (the flat layout lists) are not what the change guard compares.
        self.rebuild_topbar_action_stack(mode, force=True)

    def workspace_toolbar_non_overflow_items(self) -> set[str]:
        return {"ai_status", "search", "address"}

    def workspace_toolbar_minimum_width(self, widget: QWidget, item_id: str) -> int:
        if item_id == "search":
            return max(140, widget.minimumWidth(), widget.minimumSizeHint().width())
        if item_id == "address":
            return max(280, widget.minimumWidth(), widget.minimumSizeHint().width())
        if item_id == "selection_count":
            return max(76, widget.minimumWidth(), widget.sizeHint().width())
        return max(widget.minimumWidth(), widget.minimumSizeHint().width(), widget.sizeHint().width())

    def workspace_toolbar_required_width(self, mode: str, hidden_items: set[str]) -> int:
        if mode == "ai":
            layout = self._window.ai_toolbar_layout
        else:
            layout = self._window.manual_toolbar_layout
            mode = "manual"
        margins = layout.contentsMargins()
        item_ids = [
            item_id
            for item_id in self._window._workspace_toolbar_layouts.get(mode, ())
            if item_id not in hidden_items and not (item_id == "ai_status" and not getattr(self._window, "_ai_status_visible", True))
        ]
        width = margins.left() + margins.right()
        spacing = layout.spacing()
        visible_count = len(item_ids)
        if hidden_items:
            overflow_button = self._window._workspace_toolbar_overflow_buttons.get(mode)
            if overflow_button is not None:
                visible_count += 1
                width += max(
                    overflow_button.minimumWidth(),
                    overflow_button.minimumSizeHint().width(),
                    overflow_button.sizeHint().width(),
                )
        if visible_count > 1:
            width += (visible_count - 1) * spacing
        widgets = self._window._workspace_toolbar_item_widgets.get(mode, {})
        for item_id in item_ids:
            widget = widgets.get(item_id)
            if widget is None:
                continue
            width += self.workspace_toolbar_minimum_width(widget, item_id)
        return width

    def schedule_workspace_toolbar_overflow_update(self, mode: str) -> None:
        normalized = mode if mode == "ai" else "manual"
        if normalized in self._workspace_toolbar_overflow_update_pending:
            return
        self._workspace_toolbar_overflow_update_pending.add(normalized)
        QTimer.singleShot(0, lambda target=normalized: self.apply_workspace_toolbar_overflow(target))

    def apply_workspace_toolbar_overflow(self, mode: str) -> None:
        normalized = mode if mode == "ai" else "manual"
        self._workspace_toolbar_overflow_update_pending.discard(normalized)
        toolbar = self._window.ai_toolbar if normalized == "ai" else self._window.manual_toolbar
        layout = self._window.ai_toolbar_layout if normalized == "ai" else self._window.manual_toolbar_layout
        available_width = toolbar.width()
        if available_width <= 0:
            return

        overflow_candidates = [
            item_id
            for item_id in reversed(self._window._workspace_toolbar_layouts.get(normalized, ()))
            if item_id not in self.workspace_toolbar_non_overflow_items()
        ]
        hidden_items: set[str] = set()
        required_width = self.workspace_toolbar_required_width(normalized, hidden_items)
        for item_id in overflow_candidates:
            if required_width <= available_width:
                break
            hidden_items.add(item_id)
            required_width = self.workspace_toolbar_required_width(normalized, hidden_items)

        widgets = self._window._workspace_toolbar_item_widgets.get(normalized, {})
        layout_items = tuple(self._window._workspace_toolbar_layouts.get(normalized, ()))
        hidden_in_display_order = tuple(
            item_id for item_id in layout_items if item_id in hidden_items
        )
        self._workspace_toolbar_hidden_items[normalized] = hidden_in_display_order
        for item_id in layout_items:
            widget = widgets.get(item_id)
            if widget is None:
                continue
            visible = item_id not in hidden_items
            if item_id == "ai_status" and not getattr(self._window, "_ai_status_visible", True):
                visible = False
            widget.setVisible(visible)

        overflow_button = self._window._workspace_toolbar_overflow_buttons.get(normalized)
        if overflow_button is not None:
            overflow_button.setVisible(bool(hidden_in_display_order))
            overflow_button.setEnabled(bool(hidden_in_display_order))

        layout.invalidate()

    def populate_workspace_toolbar_overflow_menu(self, mode: str) -> None:
        normalized = mode if mode == "ai" else "manual"
        menu = self._workspace_toolbar_overflow_menus.get(normalized)
        if menu is None:
            return
        menu.clear()
        widgets = self._window._workspace_toolbar_item_widgets.get(normalized, {})
        hidden_items = self._workspace_toolbar_hidden_items.get(normalized, ())
        if not hidden_items:
            empty_action = menu.addAction("No hidden toolbar items")
            empty_action.setEnabled(False)
            return

        for item_id in hidden_items:
            widget = widgets.get(item_id)
            if widget is None:
                continue
            label = self._window.WORKSPACE_TOOLBAR_ITEM_LABELS.get(item_id, item_id)
            if item_id == "selection_count" and isinstance(widget, QLabel):
                action = menu.addAction(widget.text() or label)
                action.setEnabled(False)
                continue
            if item_id == "ai_status":
                status_menu = menu.addMenu(label)
                progress_action = status_menu.addAction(self._window._ai_run.build_ai_progress_text())
                progress_action.setEnabled(False)
                for line in (self._window.ai_status_label.toolTip() or "").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    runtime_action = status_menu.addAction(line)
                    runtime_action.setEnabled(False)
                continue
            if isinstance(widget, QToolButton):
                action = widget.defaultAction()
                if action is None:
                    candidate = widget.property("workspaceAction")
                    action = candidate if isinstance(candidate, QAction) else None
                if action is not None:
                    menu.addAction(action)
                    continue
                submenu = widget.menu()
                if submenu is not None:
                    overflow_submenu = menu.addMenu(label)
                    for submenu_action in submenu.actions():
                        if submenu_action.isSeparator():
                            overflow_submenu.addSeparator()
                        else:
                            overflow_submenu.addAction(submenu_action)
                    continue
            disabled_action = menu.addAction(label)
            disabled_action.setEnabled(False)

    def set_workspace_bar_position(self, position: str) -> None:
        normalized = self._window._normalize_workspace_bar_position(position)
        if self._window._workspace_bar_position == normalized:
            return
        self._window._workspace_bar_position = normalized
        self._window._settings.setValue(self._window.WORKSPACE_BAR_POSITION_KEY, normalized)
        self.apply_workspace_bar_position()
        self._window.statusBar().showMessage(f"Workspace toolbar moved to {normalized}")

    def apply_workspace_bar_position(self) -> None:
        layout = getattr(self._window, "workspace_center_layout", None)
        workspace_bar = getattr(self._window, "workspace_bar", None)
        tool_mode_bar = getattr(self._window, "tool_mode_bar", None)
        browser_stack = getattr(self._window, "browser_stack", None)
        if layout is None or workspace_bar is None or tool_mode_bar is None or browser_stack is None:
            return
        layout.removeWidget(workspace_bar)
        layout.removeWidget(tool_mode_bar)
        layout.removeWidget(browser_stack)
        if self._window._workspace_bar_position == "bottom":
            layout.addWidget(tool_mode_bar)
            layout.addWidget(browser_stack, 1)
            layout.addWidget(workspace_bar)
        else:
            layout.addWidget(workspace_bar)
            layout.addWidget(tool_mode_bar)
            layout.addWidget(browser_stack, 1)

    def handle_workspace_toolbar_visibility_action(self, checked: bool) -> None:
        self.set_workspace_bar_state("expanded" if checked else "hidden")

    def toggle_workspace_bar_collapsed(self) -> None:
        if self._window._workspace_bar_state == "minimized":
            self.set_workspace_bar_state("expanded")
            return
        self.set_workspace_bar_state("minimized")

    def set_workspace_bar_state(self, state: str) -> None:
        # Deliberately not persisted: the workspace bar always starts hidden
        # (see its __init__ comment), so a saved value would never be read
        # back anyway.
        self._window._workspace_bar_state = self._window._normalize_workspace_bar_state(state)
        self.apply_workspace_bar_state()

    def apply_workspace_bar_state(self) -> None:
        workspace_bar = getattr(self._window, "workspace_bar", None)
        if workspace_bar is None:
            return

        hidden = self._window._workspace_bar_state == "hidden"
        minimized = self._window._workspace_bar_state == "minimized"

        workspace_bar.setVisible(not hidden)
        self._window.toolbar_stack.setVisible(not hidden and not minimized)

        toggle_text = "+" if minimized else "\u2212"
        toggle_tooltip = "Expand workspace toolbar" if minimized else "Minimize workspace toolbar"
        self._window.workspace_bar_toggle_button.setText(toggle_text)
        self._window.workspace_bar_toggle_button.setToolTip(toggle_tooltip)

        if self._window.actions is not None:
            action = self._window.actions.show_workspace_toolbar
            action.blockSignals(True)
            action.setChecked(not hidden)
            action.blockSignals(False)
        if not hidden:
            self.schedule_workspace_toolbar_overflow_update("manual")
            self.schedule_workspace_toolbar_overflow_update("ai")

    def handle_workspace_bar_drag_event(self, event) -> bool:
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._workspace_bar_drag_start = event.globalPosition().toPoint()
            self._workspace_bar_dragging = False
            return False
        if event_type == QEvent.Type.MouseMove and self._workspace_bar_drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current = event.globalPosition().toPoint()
            if not self._workspace_bar_dragging and (current - self._workspace_bar_drag_start).manhattanLength() < QApplication.startDragDistance():
                return False
            self._workspace_bar_dragging = True
            return True
        if event_type == QEvent.Type.MouseButtonRelease and self._workspace_bar_drag_start is not None:
            was_dragging = self._workspace_bar_dragging
            self._workspace_bar_drag_start = None
            self._workspace_bar_dragging = False
            if not was_dragging:
                return False
            self.snap_workspace_bar_to_release(event.globalPosition().toPoint())
            return True
        return False

    def snap_workspace_bar_to_release(self, global_pos: QPoint) -> None:
        center_widget = getattr(self._window, "workspace_docks", None)
        shell = center_widget.shell if center_widget is not None else None
        if shell is None:
            return
        top_left = shell.mapToGlobal(QPoint(0, 0))
        shell_rect = QRect(top_left, shell.size())
        midpoint = shell_rect.top() + (shell_rect.height() // 2)
        self.set_workspace_bar_position("bottom" if global_pos.y() >= midpoint else "top")

    def sync_chrome_to_manual_review(self) -> None:
        """Bring the toolbar pages, viewport, records view and action states in line with manual review.

        AI Review is retired (docs/ai_mode_retirement.md): the app always runs in manual review, so the old
        mode switch (``_set_ui_mode`` / ``_handle_mode_tab_changed``, with its AI sort, Smart Groups lockout and
        disputed-badge branches) reduces to this refresh.
        """
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        step_start = start

        def log_step(event: str, step_started: float, **fields: object) -> float:
            if not logger.enabled:
                return step_started
            now = time.perf_counter()
            logger.duration(
                event,
                (now - step_started) * 1000.0,
                to_mode="manual",
                records=len(self._window._all_records),
                visible_records=len(self._window._records),
                ai_loaded=self._window._ai_bundle is not None,
                **fields,
            )
            return now

        self._window.toolbar_stack.setCurrentIndex(0)
        action_stack = getattr(self._window, "topbar_action_stack", None)
        if action_stack is not None:
            action_stack.setCurrentIndex(0)
            self.update_topbar_overflow("manual")
        self._window.grid.set_show_ai_annotations(self._window._show_ai_tags_in_grid)
        self.schedule_workspace_toolbar_overflow_update("manual")
        step_start = log_step("mode_switch.chrome", step_start)
        self._window._refresh_viewport_mode()
        step_start = log_step("mode_switch.viewport", step_start)
        self._window._ai_run.update_ai_toolbar_state()
        step_start = log_step("mode_switch.ai_toolbar_initial", step_start)
        self.unlock_burst_toggle_actions()
        if self._window._all_records:
            self._window._apply_records_view(current_path=self._window._records_view.current_visible_record_path())
            step_start = log_step("mode_switch.records_view", step_start)
        self._window._update_action_states()
        step_start = log_step("mode_switch.action_states", step_start)
        self._window._update_status()
        step_start = log_step("mode_switch.status", step_start)
        if logger.enabled:
            logger.duration(
                "mode_switch.total",
                (time.perf_counter() - start) * 1000.0,
                to_mode="manual",
                records=len(self._window._all_records),
                visible_records=len(self._window._records),
                ai_loaded=self._window._ai_bundle is not None,
            )

    def initial_zoom_level(self) -> int:
        return self._window._columns_to_zoom_slider_value(self._window.grid.current_columns())

    def zoom_slider_value_to_columns(self, value: object) -> int:
        try:
            slider_value = int(value)
        except (TypeError, ValueError):
            slider_value = self._window._columns_to_zoom_slider_value(self._window.grid.current_columns())
        slider_value = max(0, min(100, slider_value))
        return self._window._normalize_column_count(round(8 - ((slider_value / 100) * 7)))

    def handle_zoom_slider_changed(self, value: int) -> None:
        self._window._set_column_count(self.zoom_slider_value_to_columns(value), sync_slider=False)

    def unlock_burst_toggle_actions(self) -> None:
        """Smart Groups / Smart Stacks are always available: put their enabled state and tooltip back to the base."""
        for action in (getattr(self._window.actions, "burst_groups", None), getattr(self._window.actions, "burst_stacks", None)):
            if action is None:
                continue
            action.setEnabled(True)
            base = action.property("imageTriageBaseText")
            action.setToolTip(base if isinstance(base, str) and base else action.text())

    def show_main_menu_popup(self, anchor: QWidget) -> None:
        """Pop up the application's menu-bar menus from the top-bar ☰ button."""
        menu = QMenu(self._window)
        for action in self._window.menuBar().actions():
            menu.addAction(action)
        # Text size from MENU_ITEM_TEXT_H. Only this dropdown and its submenus
        # (the hidden menu bar's menus, reachable only from here) take it;
        # right-click menus elsewhere keep the default.
        size = layout_ratios.ratio_px(
            layout_ratios.MENU_ITEM_TEXT_H, max(1, self._window.height()), minimum=layout_ratios.MIN_TEXT_PX
        )
        self.size_menu_tree(menu, f"QMenu {{ font-size: {size}px; }}")
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def size_menu_tree(self, menu: QMenu, sheet: str, seen: set[int] | None = None) -> None:
        seen = set() if seen is None else seen
        if id(menu) in seen:
            return
        seen.add(id(menu))
        # Some menus built for the hidden menu bar are torn down and rebuilt,
        # leaving wrappers whose C++ side is gone; skip those instead of raising.
        try:
            if menu.styleSheet() != sheet:
                menu.setStyleSheet(sheet)
            actions = menu.actions()
        except RuntimeError:
            return
        for action in actions:
            try:
                submenu = action.menu()
            except RuntimeError:
                continue
            if isinstance(submenu, QMenu):
                self.size_menu_tree(submenu, sheet, seen)

    def navigate_history(self, delta: int) -> None:
        """Folder back/forward navigation backed by visited-folder stacks."""
        back = getattr(self._window, "_nav_back", None)
        forward = getattr(self._window, "_nav_forward", None)
        if back is None or forward is None:
            return
        if delta < 0:
            if not back:
                return
            target = back.pop()
            if self._window._current_folder:
                forward.append(self._window._current_folder)
        else:
            if not forward:
                return
            target = forward.pop()
            if self._window._current_folder:
                back.append(self._window._current_folder)
        self._nav_suppress_history = True
        try:
            self._window._select_folder(target)
        finally:
            self._nav_suppress_history = False
        self._window._update_nav_history_buttons()
