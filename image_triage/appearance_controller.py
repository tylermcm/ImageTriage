"""Look and scale: the theme and appearance mode, display profile, chrome size and text ratios, icon colouring and rendering, layout ratios, breadcrumb and left-rail metrics. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.5)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os

from PySide6.QtCore import QObject, QPoint, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QApplication, QToolButton, QVBoxLayout, QWidget
from collections import deque

from .ui import AppearanceMode, apply_gamma, build_app_palette, build_app_stylesheet, default_theme, parse_appearance_mode, resolve_theme
from .ui import layout_ratios
from .ui.backdrop import paint_backdrop, theme_has_backdrop
from .ui.display_metrics import DisplayProfile, STANDARD_DISPLAY, display_profile_for_preference
from .ui.nav_rail import ICON_PX as NAV_RAIL_ICON_PX
from .ui.prototype_style import NAV_ICON_ASSETS, FolderTreeView, folder_icon_pixmap, library_icon_pixmap, nav_icon_pixmap, pocketdrop_icon_pixmap, sidebar_people_icon_pixmap, sidebar_projects_icon_pixmap, rail_tool_pixmap, tool_icon_mark, trim_to_alpha

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


# TEMPORARY: the window is translucent so it can be laid over the design
# reference while the layout ratios are tuned. Set this back to 1.0 when done;
# IMAGE_TRIAGE_OPACITY overrides it without editing (e.g. 1 for opaque).
WINDOW_OPACITY = 1


def window_opacity() -> float:
    raw = os.environ.get("IMAGE_TRIAGE_OPACITY")
    try:
        value = float(raw) if raw else WINDOW_OPACITY
    except ValueError:
        value = WINDOW_OPACITY
    return min(1.0, max(0.1, value))


class AppearanceController(QObject):
    """Look and scale: the theme and appearance mode, display profile, chrome size and text ratios, icon colouring and rendering, layout ratios, breadcrumb and left-rail metrics. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.5)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window

    def update_download_icon(self, color: QColor) -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, 5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(32, 10, 32, 38)
        painter.drawLine(20, 28, 32, 40)
        painter.drawLine(44, 28, 32, 40)
        painter.drawLine(17, 50, 47, 50)
        painter.end()
        return QIcon(pixmap)

    def pane_toggle_icon(self, side: str) -> QIcon:
        """Return a mirrored panel glyph whose bright side means visible."""
        theme = getattr(self._window, "_theme", None) or default_theme()

        def draw(outline: QColor, panel: QColor) -> QPixmap:
            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(outline, 3)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRect(8, 12, 47, 39), 4, 4)
            painter.fillRect(QRect(12, 16, 13, 31), panel)
            painter.end()
            if side == "right":
                return pixmap.transformed(QTransform().scale(-1, 1))
            return pixmap

        icon = QIcon()
        colors = (
            (QIcon.Mode.Normal, theme.text_muted.qcolor(), theme.text_muted.qcolor()),
            (QIcon.Mode.Active, theme.text_secondary.qcolor(), theme.text_secondary.qcolor()),
            (QIcon.Mode.Disabled, theme.text_disabled.qcolor(), theme.text_disabled.qcolor()),
        )
        for mode, outline, inactive_panel in colors:
            icon.addPixmap(draw(outline, inactive_panel), mode, QIcon.State.Off)
            active_panel = theme.text_primary.qcolor() if mode != QIcon.Mode.Disabled else inactive_panel
            icon.addPixmap(draw(outline, active_panel), mode, QIcon.State.On)
        return icon

    def directory_nav_icon(self, direction: str, color: QColor) -> QIcon:
        pixmap = QPixmap(56, 56)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, 4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if direction == "up":
            painter.drawLine(28, 12, 28, 42)
            painter.drawLine(17, 23, 28, 12)
            painter.drawLine(39, 23, 28, 12)
        else:
            painter.drawLine(28, 14, 28, 44)
            painter.drawLine(17, 33, 28, 44)
            painter.drawLine(39, 33, 28, 44)
        painter.end()
        return QIcon(pixmap)

    def refresh_directory_nav_button_icons(self) -> None:
        color = (self._window._theme or default_theme()).text_muted.qcolor()
        for button in getattr(self._window, "_directory_up_buttons", ()):
            button.setIcon(self.directory_nav_icon("up", color))
        for button in getattr(self._window, "_directory_down_buttons", ()):
            button.setIcon(self.directory_nav_icon("down", color))

    def schedule_layout_ratio_update(self) -> None:
        if getattr(self._window, "_layout_ratio_update_pending", False):
            return
        self._window._layout_ratio_update_pending = True
        QTimer.singleShot(0, self.apply_layout_ratios)

    def apply_layout_ratios(self) -> None:
        """Resolve the shell's proportions (layout_ratios) against the
        current window size."""
        self._window._layout_ratio_update_pending = False
        width, height = self._window.width(), self._window.height()
        if width <= 0 or height <= 0 or not hasattr(self._window, "app_top_bar"):
            return
        px = layout_ratios.ratio_px
        self._window.app_top_bar.setFixedHeight(px(layout_ratios.TOP_BAR_H, height, minimum=36))
        for control in getattr(self._window._toolbar, "_window_control_buttons", {}).values():
            control.setFixedSize(round(self._window.app_top_bar.height() * 0.9), self._window.app_top_bar.height())
        search_width = px(layout_ratios.SEARCH_W, width, minimum=200)
        self._window._toolbar.app_search_box.setMinimumWidth(min(240, search_width))
        self._window._toolbar.app_search_box.setMaximumWidth(search_width)
        self.apply_chrome_text_ratios(width, height)
        # Before the toolbar profile below: its button height reads the floating
        # bar's padding, which this sets.
        self.apply_chrome_size_ratios(width, height)
        self._window.left_nav_rail.apply_width(px(layout_ratios.RAIL_W, width, minimum=56))
        self.apply_left_rail_metrics()
        drive_row = px(layout_ratios.DRIVE_ROW_H, height, minimum=34)
        folder_text = px(layout_ratios.FOLDER_TEXT_H, height, minimum=layout_ratios.MIN_TEXT_PX)
        drive_text = px(layout_ratios.DRIVE_TEXT_H, height, minimum=layout_ratios.MIN_TEXT_PX)
        for tree in (self._window.drive_list, self._window.folder_tree):
            tree.set_drive_row_height(drive_row)
            tree.set_text_sizes(folder_text, drive_text)
        self._window.left_settings_bar.setFixedHeight(px(layout_ratios.SETTINGS_BAR_H, height, minimum=40))
        if self._window.workspace_docks is not None:
            # Hairline dividers: the panes' shares are measured edge to edge, so
            # a wide handle would eat into the grid.
            self._window.workspace_docks.splitter.setHandleWidth(1)
            self._window.workspace_docks.apply_width_ratios(self._window._settings_ctl.pane_width_ratios(), width)
        if self._window.inspector_panel is not None:
            self._window.inspector_panel.set_ai_box_size(
                px(layout_ratios.AI_BOX_W, width, minimum=180),
                px(layout_ratios.AI_BOX_H, height, minimum=120),
            )
            # After the splitter, so the label column measures the settled pane.
            self._window._display.apply_inspector_text_ratios(width, height)
        base = getattr(self._window, "_display_profile", None) or STANDARD_DISPLAY
        caption_scale = self._window._toolbar.toolbar_profile().topbar_glyph_size / max(1, base.topbar_glyph_size)
        self._window._toolbar.toolbar_strip.setStyleSheet(
            f"QLabel#appTopBarButtonCaption {{ font-size: {round(10 * caption_scale)}px; }}"
            if caption_scale > 1.01
            else ""
        )
        # Not forced: this runs on every layout pass and nearly all of them leave
        # the bar's inputs unchanged, so the change guard turns them into no-ops.
        self._window._toolbar.rebuild_topbar_action_stack()
        toolbar_profile = self._window._toolbar.toolbar_profile()
        for button, _item_id in getattr(self._window._toolbar, "_topbar_labeled_nav_buttons", ()):
            self._window._resize_topbar_button(button, toolbar_profile)
        self._window._toolbar.position_floating_toolbar()
        self._window._display.schedule_app_bar_alignment()

    def refresh_breadcrumb(self) -> None:
        self._window._navigation.sync_drive_sections()
        crumb = getattr(self._window._toolbar, "app_breadcrumb", None)
        if crumb is None:
            return
        if self._window._scope_kind == "folder" and self._window._current_folder:
            crumb.set_path(self._window._current_folder)
        else:
            crumb.set_label(self._window._projects.scope_display_label())

    def end_breadcrumb_path_edit(self) -> None:
        stack = getattr(self._window._toolbar, "app_crumb_stack", None)
        if stack is None or stack.currentWidget() is self._window._toolbar.app_breadcrumb:
            return
        stack.setCurrentWidget(self._window._toolbar.app_breadcrumb)
        self.refresh_breadcrumb()

    def maybe_end_breadcrumb_path_edit(self) -> None:
        # Focus can hop to the folder-suggestion popup while typing; only
        # fold back to the breadcrumb once focus has really left the path box.
        combo = self._window.topbar_path_combo
        focused = QApplication.focusWidget()
        if QApplication.activePopupWidget() is not None:
            return
        if focused is not None and (focused is combo or combo.isAncestorOf(focused)):
            return
        self.end_breadcrumb_path_edit()

    def apply_chrome_icon_scale(self) -> None:
        """Restore the standard icon scale for fixed chrome controls."""
        for button, base in getattr(self._window, "_left_settings_buttons", ()):
            button.setIconSize(QSize(base, base))
        for button, base in getattr(self._window._toolbar, "_topbar_nav_buttons", ()):
            font = button.font()
            font.setPixelSize(base)
            button.setFont(font)

    def schedule_display_profile_update(self) -> None:
        if self._window._display_profile_update_pending:
            return
        self._window._display_profile_update_pending = True
        QTimer.singleShot(0, self.apply_display_profile)

    def apply_display_profile(self) -> None:
        self._window._display_profile_update_pending = False
        container = getattr(self._window, "central_container", None)
        width = int(container.width()) if container is not None and container.width() > 0 else int(self._window.width())
        height = int(container.height()) if container is not None and container.height() > 0 else int(self._window.height())
        profile = display_profile_for_preference(width, height, self._window._interface_size)
        if profile == self._window._display_profile:
            return
        self._window._display_profile = profile

        if container is not None:
            layout = container.layout()
            if layout is not None:
                # Edge-to-edge shell: panels meet at hairlines, no outer gutter.
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(0)
        docks = getattr(self._window, "workspace_docks", None)
        if docks is not None:
            docks.apply_display_profile(profile)
        self.apply_main_chrome_display_profile(profile)
        preview = self._window._preview_ctl.preview_if_built()
        if preview is not None:
            preview.apply_display_profile(profile)

    def apply_main_chrome_display_profile(self, profile: DisplayProfile) -> None:
        """Apply bounded profile metrics to the production window chrome."""

        bar = getattr(self._window, "app_top_bar", None)
        if bar is not None and bar.layout() is not None:
            bar.layout().setContentsMargins(
                profile.shell_margin,
                0,
                0 if getattr(self._window._toolbar, "_window_control_buttons", None) else profile.shell_margin + 2,
                0,
            )
            bar.layout().setSpacing(profile.inspector_spacing)
        search = getattr(self._window._toolbar, "topbar_search_field", None)
        if search is not None:
            search.setMinimumWidth(profile.topbar_search_min_width)
            search.setMaximumWidth(profile.topbar_search_max_width)
        zoom = getattr(self._window._toolbar, "topbar_zoom_slider", None)
        if zoom is not None:
            zoom.setFixedWidth(profile.topbar_zoom_width)
        path = getattr(self._window, "topbar_path_combo", None)
        if path is not None:
            path.setMinimumWidth(profile.topbar_path_min_width)
            path.setMaximumWidth(profile.topbar_path_max_width)
        for button, _base in getattr(self._window._toolbar, "_topbar_nav_buttons", ()):
            button.setFixedSize(profile.topbar_nav_button_size, profile.topbar_nav_button_size)
            font = button.font()
            font.setPixelSize(profile.topbar_nav_font_size)
            button.setFont(font)
        toolbar_profile = self._window._toolbar.toolbar_profile()
        for button, _item_id in getattr(self._window._toolbar, "_topbar_labeled_nav_buttons", ()):
            self._window._resize_topbar_button(button, toolbar_profile)
        for button in getattr(self._window._toolbar, "_topbar_pane_buttons", {}).values():
            hover_width = profile.topbar_slot_button_width + 2 * profile.topbar_hover_margin
            hover_height = profile.topbar_button_height + 2 * profile.topbar_hover_margin
            button.setFixedSize(hover_width, hover_height)
            button.setIconSize(QSize(profile.topbar_glyph_size + 2, profile.topbar_glyph_size + 2))
        nav_layout = getattr(self._window._toolbar, "_topbar_nav_layout", None)
        if nav_layout is not None:
            nav_layout.setSpacing(profile.topbar_slot_spacing)
        for grid in getattr(self._window._toolbar, "_topbar_action_layouts", {}).values():
            grid.setHorizontalSpacing(profile.topbar_slot_spacing)
        if getattr(self._window._toolbar, "_topbar_action_layouts", None):
            # Forced: _apply_display_profile only gets here for a profile that
            # differs from the last one, which is exactly what the bar must follow.
            self._window._toolbar.rebuild_topbar_action_stack(force=True)
        self._window._toolbar.size_view_controls()
        search_field = getattr(self._window._toolbar, "topbar_search_field", None)
        if search_field is not None:
            search_field.setMinimumWidth(180)
            search_field.setMaximumWidth(16777215)
        if path is not None:
            path.setMaximumWidth(16777215)

    def paint_grid_backdrop(self, painter: QPainter, rect: QRect) -> None:
        theme = self._window._theme
        if theme is None:
            return
        origin = self._window.grid.viewport().mapTo(self._window, QPoint(0, 0))
        paint_backdrop(painter, theme, self._window.size(), origin, rect)

    def drive_glyph_icon(self, path: str) -> QIcon | None:
        """Fluent drive glyphs (disk, removable card, network share) in the
        theme's meter colour, instead of the shell's drive icons."""
        if not path:
            return None
        drive_type = 3
        if os.name == "nt":
            try:
                drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(os.path.splitdrive(path)[0] + "\\"))  # type: ignore[attr-defined]
            except (AttributeError, OSError, ValueError):
                drive_type = 3
        glyph = {2: "E7F8", 4: "E968"}.get(drive_type, "EDA2")
        theme = self._window._theme or default_theme()
        colour = theme.accent_hover.qcolor()
        cache = self._window.__dict__.setdefault("_drive_glyph_icon_cache", {})
        key = (glyph, colour.rgba())
        if key not in cache:
            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            font = QFont("Segoe Fluent Icons")
            font.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
            font.setPixelSize(56)
            painter.setFont(font)
            painter.setPen(colour)
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, chr(int(glyph, 16)))
            painter.end()
            cache[key] = self._window._trim_icon_transparency(QIcon(pixmap), padding=2)
        return cache[key]

    def fluent_filled_icon(self, primary: str, color: QColor) -> QIcon:
        """Render a Fluent glyph as a solid filled silhouette (the enclosed
        interior flood-filled), rather than the default outline/stroke look."""
        size = 64
        glyph_img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        glyph_img.fill(0)
        painter = QPainter(glyph_img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        font_family = "Segoe MDL2 Assets" if len(primary) > 2 else "Segoe UI"
        font = QFont(font_family, 42)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        text = chr(int(primary, 16)) if len(primary) > 2 else primary
        painter.drawText(glyph_img.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

        threshold = 70
        ink = bytearray(size * size)
        for y in range(size):
            for x in range(size):
                if glyph_img.pixelColor(x, y).alpha() >= threshold:
                    ink[y * size + x] = 1

        # Flood-fill the exterior (transparent region reachable from the border).
        exterior = bytearray(size * size)
        stack = deque()
        for x in range(size):
            for y in (0, size - 1):
                idx = y * size + x
                if not ink[idx] and not exterior[idx]:
                    exterior[idx] = 1
                    stack.append((x, y))
        for y in range(size):
            for x in (0, size - 1):
                idx = y * size + x
                if not ink[idx] and not exterior[idx]:
                    exterior[idx] = 1
                    stack.append((x, y))
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < size and 0 <= ny < size:
                    idx = ny * size + nx
                    if not ink[idx] and not exterior[idx]:
                        exterior[idx] = 1
                        stack.append((nx, ny))

        def render(fill_color: QColor) -> QPixmap:
            # Silhouette = everything that is not exterior (ink + enclosed interior).
            result = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
            result.fill(0)
            fill = QColor(fill_color)
            for y in range(size):
                base = y * size
                for x in range(size):
                    if not exterior[base + x]:
                        result.setPixelColor(x, y, fill)
            # Carve the original strokes back out so internal detail (lines,
            # holes, edges) reads as negative space instead of a solid blob.
            if primary not in self._window.FILLED_ICON_SKIP_CARVE:
                carve = QPainter(result)
                carve.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
                carve.drawImage(0, 0, glyph_img)
                carve.end()
            return QPixmap.fromImage(result)

        theme = getattr(self._window, "_theme", None) or default_theme()
        icon = QIcon(render(color))
        icon.addPixmap(render(theme.text_primary.qcolor()), QIcon.Mode.Active, QIcon.State.Off)
        icon.addPixmap(render(theme.accent.qcolor()), QIcon.Mode.Normal, QIcon.State.On)
        icon.addPixmap(render(theme.accent_hover.qcolor()), QIcon.Mode.Active, QIcon.State.On)
        icon.addPixmap(render(theme.text_disabled.qcolor()), QIcon.Mode.Disabled)
        return icon

    def chrome_icon_color(self) -> QColor:
        """Muted grey used for the left rail / settings-bar glyphs (instead of
        the bright off-white default). Falls back to a constant because the theme
        is not resolved yet when these chrome buttons are first built."""
        theme = getattr(self._window, "_theme", None) or default_theme()
        return theme.text_secondary.qcolor() if not theme.is_dark else theme.text_muted.qcolor()

    def rail_tool_icon(self, item_id: str, box: int, max_side: int) -> tuple[QIcon, int]:
        """A pinned tool's icon at the terminal mark's size, and the icon size
        to show it at. Artwork and glyphs alike are trimmed to their ink first
        (see rail_tool_pixmap), so every pin lands at the same weight."""
        theme = getattr(self._window, "_theme", None) or default_theme()
        ratio = self.icon_ratio()
        colour = theme.text_secondary.qcolor()
        disabled = theme.text_muted.qcolor() if not theme.is_dark else theme.text_disabled.qcolor()
        # Glyph colours match _fluent_toolbar_icon's normal, hover and disabled.
        states = (
            (QIcon.Mode.Normal, colour, theme.accent.qcolor()),
            (QIcon.Mode.Active, theme.text_primary.qcolor(), theme.accent_hover.qcolor()),
            (QIcon.Mode.Disabled, disabled, disabled),
        )
        cache = self._window.__dict__.setdefault("_rail_tool_icon_cache", {})
        key = (item_id, box, max_side, ratio, tuple((a.rgba(), b.rgba()) for _mode, a, b in states))
        cached = cache.get(key)
        if cached is not None:
            return cached
        mark = tool_icon_mark(item_id)
        glyphs = self._window.WORKSPACE_TOOLBAR_FLUENT_ICONS.get(item_id)
        if mark is not None:
            pixmap, side = rail_tool_pixmap(mark, box, ratio=ratio, max_side=max_side, tint=colour.name())
            result = (QIcon(pixmap), side)
        elif glyphs is not None:
            icon = QIcon()
            side = box
            for mode, primary_colour, secondary_colour in states:
                # Drawn large so trimming and resampling keep the strokes crisp.
                drawn = self._window._render_fluent_glyphs(
                    glyphs[0], glyphs[1], primary_colour, secondary_colour, scale=4
                ).toImage()
                trimmed = trim_to_alpha(drawn)
                if trimmed is None:
                    continue
                pixmap, side = rail_tool_pixmap(trimmed, box, ratio=ratio, max_side=max_side)
                icon.addPixmap(pixmap, mode)
            result = (icon, side)
        else:
            result = (QIcon(), box)
        cache[key] = result
        return result

    def refresh_themed_chrome_icons(self) -> None:
        self._window.__dict__.pop("_fluent_toolbar_icon_cache", None)

        for button, _base in getattr(self._window, "_left_settings_buttons", ()):
            glyph = button.property("fluentGlyph")
            if isinstance(glyph, str) and glyph:
                button.setIcon(self.fluent_filled_icon(glyph, self.chrome_icon_color()))

        self.refresh_left_sidebar_icons()
        menu_button = getattr(self._window._toolbar, "app_menu_button", None)
        if menu_button is not None:
            menu_button.setIcon(self._window._toolbar.topbar_nav_icon("menu"))

        for button, item_id in getattr(self._window._toolbar, "_topbar_labeled_nav_buttons", ()):
            glyph = button.findChild(QToolButton, "appTopBarGlyph")
            if glyph is not None:
                glyph.setIcon(self._window._toolbar.topbar_nav_icon(item_id))
        for key, button in getattr(self._window._toolbar, "_topbar_pane_buttons", {}).items():
            button.setIcon(self.pane_toggle_icon("left" if key == "library" else "right"))

        for widgets in getattr(self._window, "_workspace_toolbar_item_widgets", {}).values():
            for item_id, widget in widgets.items():
                if isinstance(widget, QToolButton):
                    self._window._toolbar.configure_workspace_toolbar_button(
                        widget,
                        item_id=item_id,
                        text=self._window.WORKSPACE_TOOLBAR_ITEM_LABELS.get(item_id, item_id),
                    )
        for button in getattr(self._window, "_workspace_toolbar_overflow_buttons", {}).values():
            if isinstance(button, QToolButton):
                self._window._toolbar.configure_workspace_toolbar_button(button, item_id="more", text="More")

        if hasattr(self._window, "topbar_action_stack"):
            # Forced: the icon cache was just cleared, an input the change guard
            # cannot see, and this is a theme / gamma / icon refresh.
            self._window._toolbar.rebuild_topbar_action_stack(force=True)

    def refresh_left_sidebar_icons(self) -> None:
        theme = getattr(self._window, "_theme", None) or default_theme()
        accent = theme.accent.qcolor()
        muted = theme.text_muted.qcolor()
        for tree in (getattr(self._window, "folder_tree", None), getattr(self._window, "drive_list", None)):
            if not isinstance(tree, FolderTreeView):
                continue
            selected_fill = theme.selection_fill.qcolor()
            hovered_fill = QColor(selected_fill)
            hovered_fill.setAlpha(max(1, selected_fill.alpha() // 2))
            tree.set_navigation_colors(selected_fill, hovered_fill)
            # Drive meters: accent into the backdrop's second glow when the
            # theme has one (Indigo: violet into teal), plain accent otherwise.
            fill_start = theme.meter_start.qcolor() if theme.meter_start else accent
            fill_end = theme.meter_end.qcolor() if theme.meter_end else accent
            tree.set_usage_bar_colors(theme.text_primary.with_alpha(22).qcolor(), fill_start, fill_end)
            self._window.__dict__.pop("_drive_glyph_icon_cache", None)
            tree.set_drive_icon_provider(self.drive_glyph_icon)
        self.apply_left_rail_label_colors()
        self._window._handoff.apply_pocketdrop_background()
        if getattr(self._window._toolbar, "left_rail_add_button", None) is not None:
            self._window._toolbar.rebuild_pinned_tools()
        refresh_button = getattr(self._window, "drives_refresh_button", None)
        if refresh_button is not None:
            refresh_button.setIcon(self._window._toolbar.fluent_toolbar_icon("E72C", color=muted))
        face_header = getattr(self._window, "face_groups_header", None)
        if face_header is not None:
            face_header.set_icon(QIcon(sidebar_people_icon_pixmap(21, accent.name())))
        projects_header = getattr(self._window, "projects_header", None)
        if projects_header is not None:
            projects_header.set_icon(QIcon(sidebar_projects_icon_pixmap(21, accent.name())))
        for button in (
            getattr(self._window, "face_groups_add_button", None),
            getattr(self._window, "projects_add_button", None),
            getattr(self._window, "folders_add_button", None),
        ):
            if button is not None:
                button.setIcon(
                    self._window._toolbar.fluent_toolbar_icon("E710", color=self.chrome_icon_color())
                )
        search_action = getattr(self._window, "_face_groups_search_action", None)
        if search_action is not None:
            search_action.setIcon(self._window._toolbar.fluent_toolbar_icon("E721", color=muted))
        rail = getattr(self._window, "left_nav_rail", None)
        if rail is not None:
            rail.refresh_icons()

    def build_left_nav_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("libraryStack")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        return page

    def left_nav_icon(self, icon_id: str, selected: bool) -> QIcon:
        # The same drawn icons the page headers use, so the rail and the page it
        # opens match and nothing depends on a glyph being present in the font.
        theme = getattr(self._window, "_theme", None) or default_theme()
        colour = (theme.accent if selected else theme.text_muted).qcolor().name()
        # Sized to the rail, so the mark grows with it instead of being
        # upscaled from a fixed pixmap.
        rail = getattr(self._window, "left_nav_rail", None)
        size = rail.metrics().icon_px if rail is not None else NAV_RAIL_ICON_PX
        supplied = nav_icon_pixmap(
            icon_id, self.nav_icon_box(icon_id, size), colour, ratio=self.icon_ratio()
        )
        if supplied is not None:
            return QIcon(supplied)
        painter = {
            "folder": folder_icon_pixmap,
            "library": library_icon_pixmap,
            "faces": sidebar_people_icon_pixmap,
            "people": sidebar_people_icon_pixmap,
            "collections": sidebar_projects_icon_pixmap,
            "pocketdrop": pocketdrop_icon_pixmap,
        }.get(icon_id, folder_icon_pixmap)
        return QIcon(painter(self.nav_icon_box(icon_id, size), colour))

    def apply_chrome_text_ratios(self, width: int, height: int) -> None:
        """Size the top bar's text and the pane headings from layout_ratios.

        Each goes on through the widget's own stylesheet, which outranks the
        application one where these sizes would otherwise be pinned.
        """
        px = layout_ratios.ratio_px
        floor = layout_ratios.MIN_TEXT_PX

        def text_px(ratio: float) -> int:
            return px(ratio, height, minimum=floor)

        # The Menu button is icon only, so it has no text size (MENU_ICON_H).
        glyph_floor = layout_ratios.MIN_GLYPH_PX

        def glyph_px(ratio: float) -> int:
            return px(ratio, height, minimum=glyph_floor)

        crumb = getattr(self._window._toolbar, "app_breadcrumb", None)
        if crumb is not None:
            # Named selectors, not a bare font-size: a bare one cascades into the
            # chevrons between folders and swells them to the text size.
            sheet = (
                "QToolButton#breadcrumbSegment, QToolButton#breadcrumbCurrent,"
                " QLabel#breadcrumbCurrentLabel"
                f" {{ font-size: {text_px(layout_ratios.BREADCRUMB_TEXT_H)}px; }}"
                " QLabel#breadcrumbChevron"
                f" {{ font-size: {glyph_px(layout_ratios.BREADCRUMB_CHEVRON_H)}px; }}"
            )
            if crumb.styleSheet() != sheet:
                crumb.setStyleSheet(sheet)
        search_box = getattr(self._window._toolbar, "app_search_box", None)
        if search_box is not None:
            search_height = px(layout_ratios.SEARCH_H, height, minimum=24)
            field_px = text_px(layout_ratios.SEARCH_TEXT_H)
            sheet = (
                f"QFrame#appSearchBox {{ min-height: {search_height}px;"
                f" max-height: {search_height}px; }}"
                f" QLineEdit#workspaceSearchField {{ font-size: {field_px}px; }}"
                " QLabel#appSearchGlyph"
                f" {{ font-size: {glyph_px(layout_ratios.SEARCH_GLYPH_H)}px; }}"
                " QToolButton#appSearchKeyHint"
                f" {{ font-size: {glyph_px(layout_ratios.SEARCH_HINT_H)}px; }}"
            )
            if search_box.styleSheet() != sheet:
                search_box.setStyleSheet(sheet)
        heading_px = text_px(layout_ratios.SECTION_TITLE_H)
        for header in (
            getattr(self._window, "drives_header", None),
            getattr(self._window, "folders_header", None),
            getattr(self._window, "face_groups_header", None),
            getattr(self._window, "projects_header", None),
        ):
            title = getattr(header, "title", None)
            if title is not None:
                self._window._set_widget_font_px(title, heading_px)

    def apply_chrome_size_ratios(self, width: int, height: int) -> None:
        """Size the shell's icons, buttons and spacing from layout_ratios.

        Covers what the text ratios do not: the top bar's icons and window
        buttons, the pane headings, the drive meters and folder tree, the
        settings strip, the floating bar's padding and the status text.
        """
        px = layout_ratios.ratio_px
        g_floor = layout_ratios.MIN_GLYPH_PX

        def glyph(ratio: float) -> int:
            return px(ratio, height, minimum=g_floor)

        def size(button: QToolButton | None, box: int | None, icon: int) -> None:
            if button is None:
                return
            if box is not None:
                button.setFixedSize(box, box)
            button.setIconSize(QSize(icon, icon))

        # Top bar
        size(getattr(self._window._toolbar, "app_menu_button", None), None, glyph(layout_ratios.MENU_ICON_H))
        size(
            getattr(self._window._toolbar, "app_settings_button", None),
            glyph(layout_ratios.TOP_GEAR_BOX_H),
            glyph(layout_ratios.TOP_GEAR_ICON_H),
        )
        update_button = getattr(self._window, "update_download_button", None)
        if update_button is not None:
            update_button.setFixedSize(
                glyph(layout_ratios.UPDATE_BOX_W_H), glyph(layout_ratios.UPDATE_BOX_H)
            )
            update_icon = glyph(layout_ratios.UPDATE_ICON_H)
            update_button.setIconSize(QSize(update_icon, update_icon))
        window_glyph = glyph(layout_ratios.WINDOW_BUTTON_GLYPH_H)
        for control in getattr(self._window._toolbar, "_window_control_buttons", {}).values():
            self._window._set_widget_font_px(control, window_glyph)

        # Drives / Folders headings, and their refresh / + buttons
        header_height = glyph(layout_ratios.SECTION_HEADER_H)
        chevron = glyph(layout_ratios.SECTION_CHEVRON_H)
        for header in (
            getattr(self._window, "drives_header", None),
            getattr(self._window, "folders_header", None),
            getattr(self._window, "face_groups_header", None),
            getattr(self._window, "projects_header", None),
        ):
            if header is None:
                continue
            sheet = f"QWidget#navSectionHeader {{ min-height: {header_height}px; }}"
            if header.styleSheet() != sheet:
                header.setStyleSheet(sheet)
            header.chevron.setFixedSize(chevron, chevron)
        section_box = glyph(layout_ratios.SECTION_BUTTON_BOX_H)
        section_icon = glyph(layout_ratios.SECTION_BUTTON_ICON_H)
        for button in (
            getattr(self._window, "drives_refresh_button", None),
            getattr(self._window, "folders_add_button", None),
        ):
            size(button, section_box, section_icon)

        # Drive meters and the folder tree
        meter = glyph(layout_ratios.DRIVE_METER_H)
        meter_gap = px(layout_ratios.DRIVE_METER_GAP_H, height, minimum=0)
        for tree in (getattr(self._window, "drive_list", None), getattr(self._window, "folder_tree", None)):
            if tree is not None:
                tree.set_meter_metrics(meter, meter_gap)
        folder_tree = getattr(self._window, "folder_tree", None)
        if folder_tree is not None:
            folder_icon = glyph(layout_ratios.FOLDER_ICON_H)
            folder_tree.setIconSize(QSize(folder_icon, folder_icon))
            folder_tree.setIndentation(glyph(layout_ratios.FOLDER_INDENT_H))

        # Settings strip under the folder pane (the top-bar gear is sized above)
        strip_box = glyph(layout_ratios.SETTINGS_BUTTON_BOX_H)
        strip_icon = glyph(layout_ratios.SETTINGS_ICON_H)
        top_gear = getattr(self._window._toolbar, "app_settings_button", None)
        for button, _base in getattr(self._window, "_left_settings_buttons", ()):
            if button is not top_gear:
                size(button, strip_box, strip_icon)
        settings_bar = getattr(self._window, "left_settings_bar", None)
        if settings_bar is not None and settings_bar.layout() is not None:
            pad = glyph(layout_ratios.SETTINGS_PAD_H)
            settings_bar.layout().setContentsMargins(pad, pad, pad, pad)
            settings_bar.layout().setSpacing(glyph(layout_ratios.SETTINGS_GAP_H))

        # Floating bar padding (its margin, row gap and fade are positional,
        # applied in _position_floating_toolbar)
        strip_layout = getattr(self._window._toolbar, "_toolbar_strip_layout", None)
        if strip_layout is not None:
            pad_x = px(layout_ratios.FLOATING_TOOLBAR_PAD_X_H, height, minimum=0)
            pad_y = px(layout_ratios.FLOATING_TOOLBAR_PAD_Y_H, height, minimum=0)
            strip_layout.setContentsMargins(pad_x, pad_y, pad_x, pad_y)

        # Zoom slider, pane toggles and label widths in the status bar
        self._window._toolbar.size_view_controls()

        # Status bar text (the three permanent labels share one object name)
        status_px = px(layout_ratios.STATUS_TEXT_H, height, minimum=layout_ratios.MIN_TEXT_PX)
        # The message on the far left ("Ready", "Update available") is drawn by
        # the status bar itself, not a label, so it takes the bar's own font.
        status_bar = self._window.statusBar()
        status_sheet = f"QStatusBar {{ font-size: {status_px}px; }}"
        if status_bar.styleSheet() != status_sheet:
            status_bar.setStyleSheet(status_sheet)
        for label in (
            getattr(self._window, "filter_summary_label", None),
            getattr(self._window, "catalog_status_label", None),
            getattr(self._window, "cache_pipeline_label", None),
        ):
            if label is not None:
                self._window._set_widget_font_px(label, status_px)

    def apply_left_rail_label_colors(self) -> None:
        """Give the rail the same two colours its icons are tinted with."""
        rail = getattr(self._window, "left_nav_rail", None)
        if rail is None:
            return
        theme = getattr(self._window, "_theme", None) or default_theme()
        rail.set_label_colors(theme.text_muted.qcolor(), theme.accent.qcolor())

    def icon_ratio(self) -> int:
        """The device pixel ratio supplied artwork should be rasterised at.

        Building at a fixed 2x and letting Qt shrink it again on a 1x display
        resamples the art twice, which turns a thin outline to mush; one pass
        straight to the size it will be drawn at keeps it sharp.
        """
        try:
            return max(1, round(self._window.devicePixelRatioF()))
        except (AttributeError, RuntimeError):
            return 1

    def nav_icon_box(self, icon_id: str, icon_px: int) -> int:
        """The icon box that puts this mark's ink at ``icon_px``.

        Supplied artwork is trimmed and fitted to its box, so it needs no
        correction; the drawn painters each pad by their own amount.
        """
        if icon_id in NAV_ICON_ASSETS:
            return icon_px
        return round(icon_px / self._window.NAV_ICON_INK_FILL.get(icon_id, 1.0))

    def show_left_nav_page(self, key: str) -> None:
        page = getattr(self._window, "_left_nav_page_widgets", {}).get(key)
        if page is None:
            return
        self._window.left_nav_pages.setCurrentWidget(page)
        if self._window.left_nav_rail.current() != key:
            self._window.left_nav_rail.set_current(key, emit=False)
        self._window._settings.setValue(self._window.LEFT_NAV_PAGE_KEY, key)

    def apply_left_rail_metrics(self) -> None:
        """Size the pinned block to the rail, so the divider, the PINNED
        caption and the tool buttons keep the design's proportions alongside
        the destinations above them."""
        rail = getattr(self._window, "left_nav_rail", None)
        if rail is None:
            return
        metrics = rail.metrics()
        # Give each destination an icon box wide enough that its drawn ink
        # reaches the design's mark size (the painters pad by different amounts).
        for key, _label, icon_id, _tooltip in self._window.LEFT_NAV_DESTINATIONS:
            button = rail.button(key)
            if button is None:
                continue
            box = self.nav_icon_box(icon_id, metrics.icon_px)
            button.setIconSize(QSize(box, box))
        divider = getattr(self._window._toolbar, "_left_rail_divider", None)
        if divider is not None:
            divider.setFixedSize(metrics.divider_width, 1)
        label = getattr(self._window._toolbar, "_left_rail_section_label", None)
        if label is not None:
            # A widget sheet beats the application sheet, which pins this size.
            label.setStyleSheet(f"font-size: {metrics.section_label_px}px;")
        section_layout = getattr(self._window._toolbar, "_left_rail_section_layout", None)
        if section_layout is not None:
            section_layout.setContentsMargins(0, metrics.section_gap, 0, 0)
            section_layout.setSpacing(metrics.tool_gap)
        pinned_layout = getattr(self._window._toolbar, "_left_rail_pinned_layout", None)
        if pinned_layout is not None:
            pinned_layout.setSpacing(metrics.tool_gap)
        for button, item_id in getattr(self._window._toolbar, "_left_rail_tool_buttons", ()):
            # Every pin, artwork or glyph, is sized to match the terminal mark.
            icon, side = self.rail_tool_icon(
                item_id, metrics.tool_icon_px, min(metrics.tool_width, metrics.tool_height)
            )
            button.setIcon(icon)
            button.setFixedSize(metrics.tool_width, metrics.tool_height)
            button.setIconSize(QSize(side, side))
            button.setStyleSheet(
                f"QToolButton#leftRailToolButton {{ border-radius: {metrics.tool_radius}px; }}"
            )
        add_button = getattr(self._window._toolbar, "left_rail_add_button", None)
        if add_button is not None:
            add_box = min(
                round(metrics.add_icon_px / self._window.FLUENT_ICON_INK_FILL), metrics.add_size
            )
            add_button.setFixedSize(metrics.add_size, metrics.add_size)
            add_button.setIconSize(QSize(add_box, add_box))
            add_button.setStyleSheet(
                f"QToolButton#leftRailAddButton {{ border-radius: {metrics.tool_radius}px; }}"
            )

    def apply_appearance(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        self._window._theme = apply_gamma(resolve_theme(self._window._appearance_mode, app), self._window._ui_gamma)
        app.setPalette(build_app_palette(self._window._theme))
        app.setStyleSheet(build_app_stylesheet(self._window._theme))
        self.refresh_themed_chrome_icons()
        self.update_dynamic_action_icons()
        self._window._help_update.refresh_update_button_state()
        self.refresh_directory_nav_button_icons()
        if self._window.workspace_docks is not None:
            self._window.workspace_docks.apply_theme(self._window._theme)
        self._window.grid.apply_theme(self._window._theme)
        self._window.grid.set_backdrop_painter(self.paint_grid_backdrop if theme_has_backdrop(self._window._theme) else None)
        preview = self._window._preview_ctl.preview_if_built()
        if preview is not None:
            preview.apply_theme(self._window._theme)
        self._window._toolbar.schedule_workspace_toolbar_overflow_update("manual")
        self._window._toolbar.schedule_workspace_toolbar_overflow_update("ai")
        self._window._inspector.update_action_states()

    def update_dynamic_action_icons(self) -> None:
        if self._window.actions is None or self._window._theme is None:
            return
        if self._window._normalize_toolbar_style(getattr(self._window, "_toolbar_style", "text")) == "text":
            self._window.actions.undo.setIcon(QIcon())

    def set_appearance_mode(self, mode: AppearanceMode) -> None:
        normalized = mode if isinstance(mode, AppearanceMode) else parse_appearance_mode(mode)
        if self._window._appearance_mode == normalized:
            return
        self._window._appearance_mode = normalized
        self._window._settings.setValue(self._window.APPEARANCE_KEY, normalized.value)
        self.apply_appearance()
        self._window.statusBar().showMessage(f"Appearance set to {normalized.value}")
