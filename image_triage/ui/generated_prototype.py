from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .prototype_style import folder_icon_pixmap

if TYPE_CHECKING:
    from ..models import ImageRecord, SessionAnnotation
    from ..window import MainWindow


DOTS = "\u2022\u2022\u2022"
CHECK = "\u2713"


@dataclass(frozen=True, slots=True)
class PrototypeItem:
    name: str
    selected: bool
    accepted: bool
    rejected: bool
    color: QColor
    pixmap: QPixmap | None = None


@dataclass(frozen=True, slots=True)
class PrototypeMetrics:
    shell_padding: int
    body_top_gap: int
    body_gap: int
    toolbar_height: int
    toolbar_h_padding: int
    toolbar_v_padding: int
    toolbar_group_gap: int
    toolbar_button: int
    toolbar_icon_font: int
    view_group_gap: int
    zoom_width: int
    rail_width: int
    rail_button: int
    rail_padding: int
    rail_gap: int
    left_width: int
    left_padding: int
    left_section_gap: int
    tree_padding: int
    tree_row_height: int
    tree_spacing: int
    review_height: int
    review_padding: int
    review_spacing: int
    review_button: int
    bottom_bar_padding: int
    bottom_bar_gap: int
    right_width: int
    right_padding: int
    right_section_gap: int
    preview_height: int
    preview_size: QSize
    inspector_padding: int
    inspector_spacing: int
    card_width: int
    card_height: int
    card_gap: int
    card_margin: int
    card_footer_height: int
    card_padding: int
    card_badge: int
    card_checkbox: int
    card_radius: int
    card_font_size: int


NORMAL_METRICS = PrototypeMetrics(
    shell_padding=5,
    body_top_gap=4,
    body_gap=6,
    toolbar_height=58,
    toolbar_h_padding=14,
    toolbar_v_padding=8,
    toolbar_group_gap=12,
    toolbar_button=34,
    toolbar_icon_font=20,
    view_group_gap=3,
    zoom_width=84,
    rail_width=50,
    rail_button=32,
    rail_padding=9,
    rail_gap=10,
    left_width=318,
    left_padding=10,
    left_section_gap=4,
    tree_padding=10,
    tree_row_height=25,
    tree_spacing=6,
    review_height=304,
    review_padding=10,
    review_spacing=8,
    review_button=33,
    bottom_bar_padding=8,
    bottom_bar_gap=12,
    right_width=330,
    right_padding=8,
    right_section_gap=4,
    preview_height=248,
    preview_size=QSize(330, 248),
    inspector_padding=8,
    inspector_spacing=7,
    card_width=165,
    card_height=142,
    card_gap=14,
    card_margin=10,
    card_footer_height=36,
    card_padding=6,
    card_badge=13,
    card_checkbox=16,
    card_radius=8,
    card_font_size=10,
)


LARGE_METRICS = PrototypeMetrics(
    shell_padding=6,
    body_top_gap=5,
    body_gap=7,
    toolbar_height=64,
    toolbar_h_padding=18,
    toolbar_v_padding=10,
    toolbar_group_gap=16,
    toolbar_button=40,
    toolbar_icon_font=23,
    view_group_gap=4,
    zoom_width=120,
    rail_width=56,
    rail_button=36,
    rail_padding=10,
    rail_gap=13,
    left_width=374,
    left_padding=12,
    left_section_gap=5,
    tree_padding=11,
    tree_row_height=27,
    tree_spacing=7,
    review_height=356,
    review_padding=12,
    review_spacing=10,
    review_button=38,
    bottom_bar_padding=9,
    bottom_bar_gap=14,
    right_width=390,
    right_padding=9,
    right_section_gap=5,
    preview_height=294,
    preview_size=QSize(390, 294),
    inspector_padding=8,
    inspector_spacing=7,
    card_width=186,
    card_height=160,
    card_gap=18,
    card_margin=11,
    card_footer_height=40,
    card_padding=7,
    card_badge=14,
    card_checkbox=17,
    card_radius=9,
    card_font_size=10,
)


def _scale_value(value: int, scale: float, minimum: int) -> int:
    return max(minimum, int(round(value * scale)))


def _scaled_metrics(base: PrototypeMetrics, scale: float) -> PrototypeMetrics:
    right_width = _scale_value(base.right_width, scale, 270)
    preview_height = _scale_value(base.preview_height, scale, 190)
    return replace(
        base,
        shell_padding=_scale_value(base.shell_padding, scale, 4),
        body_top_gap=_scale_value(base.body_top_gap, scale, 3),
        body_gap=_scale_value(base.body_gap, scale, 4),
        toolbar_height=_scale_value(base.toolbar_height, scale, 48),
        toolbar_h_padding=_scale_value(base.toolbar_h_padding, scale, 8),
        toolbar_v_padding=_scale_value(base.toolbar_v_padding, scale, 5),
        toolbar_group_gap=_scale_value(base.toolbar_group_gap, scale, 8),
        toolbar_button=_scale_value(base.toolbar_button, scale, 28),
        toolbar_icon_font=_scale_value(base.toolbar_icon_font, scale, 15),
        view_group_gap=_scale_value(base.view_group_gap, scale, 2),
        zoom_width=_scale_value(base.zoom_width, scale, 70),
        rail_width=_scale_value(base.rail_width, scale, 44),
        rail_button=_scale_value(base.rail_button, scale, 28),
        rail_padding=_scale_value(base.rail_padding, scale, 6),
        rail_gap=_scale_value(base.rail_gap, scale, 7),
        left_width=_scale_value(base.left_width, scale, 270),
        left_padding=_scale_value(base.left_padding, scale, 8),
        left_section_gap=_scale_value(base.left_section_gap, scale, 3),
        tree_padding=_scale_value(base.tree_padding, scale, 8),
        tree_row_height=_scale_value(base.tree_row_height, scale, 22),
        tree_spacing=_scale_value(base.tree_spacing, scale, 4),
        review_height=_scale_value(base.review_height, scale, 255),
        review_padding=_scale_value(base.review_padding, scale, 8),
        review_spacing=_scale_value(base.review_spacing, scale, 6),
        review_button=_scale_value(base.review_button, scale, 28),
        bottom_bar_padding=_scale_value(base.bottom_bar_padding, scale, 6),
        bottom_bar_gap=_scale_value(base.bottom_bar_gap, scale, 10),
        right_width=right_width,
        right_padding=_scale_value(base.right_padding, scale, 6),
        right_section_gap=_scale_value(base.right_section_gap, scale, 3),
        preview_height=preview_height,
        preview_size=QSize(max(220, right_width - 2 * _scale_value(base.right_padding, scale, 6)), preview_height),
        inspector_padding=_scale_value(base.inspector_padding, scale, 6),
        inspector_spacing=_scale_value(base.inspector_spacing, scale, 5),
        card_width=_scale_value(base.card_width, scale, 126),
        card_height=_scale_value(base.card_height, scale, 110),
        card_gap=_scale_value(base.card_gap, scale, 9),
        card_margin=_scale_value(base.card_margin, scale, 7),
        card_footer_height=_scale_value(base.card_footer_height, scale, 28),
        card_padding=_scale_value(base.card_padding, scale, 4),
        card_badge=_scale_value(base.card_badge, scale, 10),
        card_checkbox=_scale_value(base.card_checkbox, scale, 12),
        card_radius=_scale_value(base.card_radius, scale, 6),
        card_font_size=_scale_value(base.card_font_size, scale, 9),
    )


class PrototypeThumbnailWall(QWidget):
    def __init__(self, items: list[PrototypeItem], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items = items
        self._card_width = NORMAL_METRICS.card_width
        self._card_height = NORMAL_METRICS.card_height
        self._gap = NORMAL_METRICS.card_gap
        self._margin = NORMAL_METRICS.card_margin
        self._footer_height = NORMAL_METRICS.card_footer_height
        self._image_padding = NORMAL_METRICS.card_padding
        self._badge_size = NORMAL_METRICS.card_badge
        self._checkbox_size = NORMAL_METRICS.card_checkbox
        self._card_radius = NORMAL_METRICS.card_radius
        self._font = QFont("Segoe UI", NORMAL_METRICS.card_font_size)
        self.setObjectName("prototypeThumbnailWall")
        self.setMinimumWidth(420)
        self.setMinimumHeight(640)

    def sizeHint(self) -> QSize:
        width = self._margin * 2 + 5 * self._card_width + 4 * self._gap
        return QSize(width, 740)

    def set_metrics(self, metrics: PrototypeMetrics) -> None:
        changed = (
            self._card_width != metrics.card_width
            or self._card_height != metrics.card_height
            or self._gap != metrics.card_gap
            or self._margin != metrics.card_margin
        )
        self._card_width = metrics.card_width
        self._card_height = metrics.card_height
        self._gap = metrics.card_gap
        self._margin = metrics.card_margin
        self._footer_height = metrics.card_footer_height
        self._image_padding = metrics.card_padding
        self._badge_size = metrics.card_badge
        self._checkbox_size = metrics.card_checkbox
        self._card_radius = metrics.card_radius
        self._font = QFont("Segoe UI", metrics.card_font_size)
        self.setMinimumWidth(max(300, self._margin * 2 + 2 * self._card_width + self._gap))
        self._sync_minimum_height()
        if changed:
            self.updateGeometry()
        self.update()

    def resizeEvent(self, event) -> None:
        self._sync_minimum_height()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#070707"))
        columns = self._columns()
        for index, item in enumerate(self._items):
            row = index // columns
            column = index % columns
            x = self._margin + column * (self._card_width + self._gap)
            y = self._margin + row * (self._card_height + self._gap)
            card_rect = QRect(x, y, self._card_width, self._card_height)
            if card_rect.intersects(event.rect()):
                self._paint_card(painter, card_rect, item, index)

    def _sync_minimum_height(self) -> None:
        columns = self._columns()
        rows = max(1, (len(self._items) + columns - 1) // columns)
        height = self._margin * 2 + rows * self._card_height + max(0, rows - 1) * self._gap
        self.setMinimumHeight(height)

    def _columns(self) -> int:
        available = max(1, self.width() - self._margin * 2)
        return max(2, min(7, (available + self._gap) // (self._card_width + self._gap)))

    def _paint_card(self, painter: QPainter, rect: QRect, item: PrototypeItem, index: int) -> None:
        border = QColor("#2f3632") if item.selected else QColor("#1f2225")
        if item.rejected:
            border = QColor("#c34c43")
        elif item.accepted:
            border = QColor("#2ad06f")

        shadow_rect = QRectF(rect.adjusted(2, 3, 2, 3))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawRoundedRect(shadow_rect, self._card_radius, self._card_radius)

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(QColor("#101010"))
        painter.drawRoundedRect(QRectF(rect), self._card_radius, self._card_radius)

        content_x = rect.left() + self._image_padding
        content_width = rect.width() - self._image_padding * 2
        image_y = rect.top() + self._image_padding
        footer_y = rect.bottom() - self._footer_height + 2
        image_rect = QRect(
            content_x,
            image_y,
            content_width,
            max(12, footer_y - image_y - 1),
        )
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(image_rect), max(4, self._card_radius - 2), max(4, self._card_radius - 2))
        painter.save()
        painter.setClipPath(clip)
        if item.pixmap is not None and not item.pixmap.isNull():
            scaled = item.pixmap.scaled(
                image_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            draw_x = image_rect.left() - max(0, scaled.width() - image_rect.width()) // 2
            draw_y = image_rect.top() - max(0, scaled.height() - image_rect.height()) // 2
            painter.drawPixmap(draw_x, draw_y, scaled)
        else:
            self._paint_placeholder_landscape(painter, image_rect, index)
        painter.restore()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(item.color)
        painter.drawRoundedRect(
            QRectF(image_rect.left() + self._image_padding, image_rect.top() + self._image_padding, self._badge_size, self._badge_size),
            2,
            2,
        )

        checkbox_rect = QRect(
            image_rect.right() - (self._checkbox_size + self._image_padding),
            image_rect.top() + self._image_padding,
            self._checkbox_size,
            self._checkbox_size,
        )
        painter.setPen(QPen(QColor("#9aa0a6"), 1))
        painter.setBrush(QColor(8, 9, 10, 170))
        painter.drawRoundedRect(QRectF(checkbox_rect), 2, 2)
        if item.selected:
            painter.setPen(QPen(QColor("#f4f6f7"), 1.2))
            painter.setFont(QFont("Segoe UI Symbol", 8))
            painter.drawText(checkbox_rect.adjusted(0, -1, 0, 0), Qt.AlignmentFlag.AlignCenter, CHECK)

        footer = QRect(
            content_x,
            footer_y,
            content_width,
            max(12, rect.bottom() - footer_y - 1),
        )
        gradient = QLinearGradient(QPointF(footer.left(), footer.top()), QPointF(footer.left(), footer.bottom()))
        gradient.setColorAt(0.0, QColor("#141313"))
        gradient.setColorAt(1.0, QColor("#141313"))
        painter.fillRect(footer, gradient)
        painter.setPen(QColor("#d5d8dc"))
        painter.setFont(self._font)
        decision = "Winner" if item.accepted else ("Reject" if item.rejected else "Unreviewed")
        painter.drawText(footer.adjusted(5, 2, -42, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, decision)
        painter.setPen(QColor("#858a90"))
        painter.setFont(self._font)
        painter.drawText(footer.adjusted(0, 2, -6, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, DOTS)

    def _paint_placeholder_landscape(self, painter: QPainter, rect: QRect, seed: int) -> None:
        palettes = (
            ("#7fa0b6", "#f0b176", "#192129", "#5b6f4b"),
            ("#102742", "#54a6b8", "#060b12", "#293d34"),
            ("#394c5b", "#c56f4f", "#1b1515", "#6f4d2f"),
            ("#0e1b28", "#7cc153", "#05080c", "#1d3b4a"),
            ("#6d775f", "#ede1c0", "#11150f", "#49613d"),
            ("#1d2332", "#986c4f", "#0a0c10", "#4d342d"),
        )
        sky, light, ground, accent = palettes[seed % len(palettes)]
        gradient = QLinearGradient(QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom()))
        gradient.setColorAt(0.0, QColor(sky))
        gradient.setColorAt(0.55, QColor(light))
        gradient.setColorAt(1.0, QColor(ground))
        painter.fillRect(rect, gradient)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent))
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        width = rect.width()
        for step in range(7):
            x = rect.left() + (width * step / 6)
            y = rect.top() + rect.height() * (0.34 + ((seed + step) % 4) * 0.08)
            path.lineTo(QPointF(x, y))
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        painter.drawPath(path)

        painter.setBrush(QColor(255, 245, 190, 90))
        radius = 10 + seed % 8
        painter.drawEllipse(QPointF(rect.left() + rect.width() * (0.2 + (seed % 5) * 0.12), rect.top() + rect.height() * 0.28), radius, radius)


class UIPrototypeWindow(QMainWindow):
    def __init__(self, owner: object | None = None) -> None:
        parent = owner if isinstance(owner, QWidget) else None
        super().__init__(parent)
        self._owner = owner
        self._items = collect_prototype_items(owner)
        self.setObjectName("generatedUIPrototypeWindow")
        self.setWindowTitle("Image Triage UI Prototype")
        self.resize(1680, 930)
        self.setMinimumSize(1180, 720)
        self.setStyleSheet(_prototype_stylesheet())
        self.setCentralWidget(self._build_shell())
        self._active_metrics: PrototypeMetrics | None = None
        self._apply_responsive_metrics()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive_metrics()

    def _responsive_metrics(self) -> PrototypeMetrics:
        if self.width() >= 1500 or self.height() >= 860:
            base = LARGE_METRICS
            scale = min(1.0, max(0.72, min(self.width() / 1920, self.height() / 1080)))
        else:
            base = NORMAL_METRICS
            scale = min(1.0, max(0.72, min(self.width() / 1500, self.height() / 860)))
        metrics = _scaled_metrics(base, scale)
        # Keep panels at a constant fraction of the window so proportions hold on
        # any resize (matching the reference layout), clamped to avoid clipping.
        width = max(self.width(), self.minimumWidth())
        left_width = min(440, max(250, int(round(width * 0.195))))
        right_width = min(440, max(262, int(round(width * 0.156))))
        preview_size = QSize(max(220, right_width - 2 * metrics.right_padding), metrics.preview_height)
        return replace(
            metrics,
            left_width=left_width,
            right_width=right_width,
            preview_size=preview_size,
        )

    def _apply_responsive_metrics(self) -> None:
        if not hasattr(self, "_top_bar"):
            return
        metrics = self._responsive_metrics()
        if getattr(self, "_active_metrics", None) == metrics:
            return
        self._active_metrics = metrics

        self._shell_layout.setContentsMargins(metrics.shell_padding, metrics.shell_padding, metrics.shell_padding, metrics.shell_padding)
        self._body_layout.setContentsMargins(0, metrics.body_top_gap, 0, 0)
        self._body_layout.setSpacing(metrics.body_gap)

        # Left padding centers the first top-bar button over the rail's top
        # button; the bar height is sized so the hamburger has equal left, top
        # and bottom padding (a clean corner) and every element matches the
        # hamburger button height.
        toolbar_left_padding = max(0, (metrics.rail_width - metrics.toolbar_button) // 2 + 1)
        toolbar_height = metrics.toolbar_button + 2 * toolbar_left_padding
        self._top_bar.setMinimumHeight(toolbar_height)
        self._top_bar.setMaximumHeight(toolbar_height)
        self._top_bar_layout.setContentsMargins(
            toolbar_left_padding,
            toolbar_left_padding,
            metrics.toolbar_h_padding,
            toolbar_left_padding,
        )
        for box in self._top_bar.findChildren(QFrame):
            if box.objectName() == "prototypeColorStripBox":
                box.setFixedHeight(metrics.toolbar_button)
        self._top_bar_layout.setSpacing(metrics.toolbar_group_gap)
        if hasattr(self, "_zoom_line"):
            self._zoom_line.setFixedWidth(metrics.zoom_width)

        self._left_region.setMinimumWidth(metrics.left_width)
        self._left_region.setMaximumWidth(metrics.left_width)
        self._left_rail.setMinimumWidth(metrics.rail_width)
        self._left_rail.setMaximumWidth(metrics.rail_width)
        # Symmetric horizontal insets small enough that the button fits inside
        # the centered cell (otherwise it overflows and pins to the left margin,
        # shifting it right of center).
        rail_h = max(2, min(metrics.rail_padding, (metrics.rail_width - 2 - metrics.rail_button) // 2))
        self._rail_layout.setContentsMargins(rail_h, metrics.rail_padding, rail_h, metrics.rail_padding)
        self._rail_layout.setSpacing(metrics.rail_gap)
        # Zero top/bottom/left margins so the rail connects flush to the panel
        # and the whole unit is colinear with the top bar and reaches the bottom.
        # Spacing is 0 so the sections connect into one panel, divided only by
        # definition lines.
        self._left_panel_layout.setContentsMargins(0, 0, metrics.left_padding, 0)
        self._left_panel_layout.setSpacing(0)
        self._folder_tree_layout.setContentsMargins(metrics.tree_padding, metrics.tree_padding, metrics.tree_padding, metrics.tree_padding)
        self._folder_tree_layout.setSpacing(metrics.tree_spacing)
        for row in self._folder_tree.findChildren(QWidget):
            if row.property("prototypeFolderRow"):
                row.setMinimumHeight(metrics.tree_row_height)
                row.setMaximumHeight(metrics.tree_row_height + 3)
        self._left_tabs.setFixedHeight(metrics.review_height)
        self._left_tabs.tabBar().setMinimumHeight(max(30, metrics.review_button - 2))
        self._review_layout.setContentsMargins(metrics.review_padding, metrics.review_padding, metrics.review_padding, metrics.review_padding)
        self._review_layout.setSpacing(metrics.review_spacing)
        self._review_command_row.setSpacing(metrics.review_spacing)
        self._bottom_bar_layout.setContentsMargins(
            metrics.bottom_bar_padding,
            metrics.bottom_bar_padding,
            metrics.bottom_bar_padding,
            metrics.bottom_bar_padding,
        )
        self._bottom_bar_layout.setSpacing(metrics.bottom_bar_gap)

        self._right_region.setMinimumWidth(metrics.right_width)
        self._right_region.setMaximumWidth(metrics.right_width)
        # Top/bottom margins are 0 so the first card is colinear with the left
        # pane top and the blank trailing card reaches the left pane bottom; the
        # right margin is 0 so the cards are colinear with the top bar's right edge.
        self._right_layout.setContentsMargins(metrics.right_padding, 0, 0, 0)
        self._right_layout.setSpacing(metrics.right_section_gap)
        self._preview_image.setFixedHeight(metrics.preview_height)
        if getattr(self, "_preview_source_pixmap", None) is not None:
            self._preview_image.setPixmap(
                self._preview_source_pixmap.scaled(
                    metrics.preview_size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self._preview_image.setPixmap(_large_placeholder_pixmap(metrics.preview_size))
        self._right_action_layout.setContentsMargins(
            max(6, metrics.inspector_padding - 3),
            max(5, metrics.inspector_padding - 5),
            max(6, metrics.inspector_padding - 3),
            max(5, metrics.inspector_padding - 5),
        )
        self._right_action_layout.setSpacing(metrics.inspector_spacing)
        for frame in self.findChildren(QFrame):
            if frame.objectName() == "prototypeInspectorBlock" and frame.layout() is not None:
                frame.layout().setContentsMargins(
                    metrics.inspector_padding,
                    metrics.inspector_padding,
                    metrics.inspector_padding,
                    metrics.inspector_padding,
                )
                frame.layout().setSpacing(metrics.inspector_spacing)

        self._thumbnail_wall.set_metrics(metrics)
        self._apply_button_metrics(metrics)

    def _apply_button_metrics(self, metrics: PrototypeMetrics) -> None:
        # Glyph size is driven here (the stylesheet no longer sets a font), using
        # a pixel size proportional to the button so icons fill the button
        # without clipping (line height stays under the button height).
        def _icon_font(button_px: int, ratio: float) -> QFont:
            font = QFont("Segoe UI Symbol")
            font.setPixelSize(max(11, int(round(button_px * ratio))))
            return font

        def _apply(button: QToolButton, size: int, ratio: float) -> None:
            button.setFixedSize(size, size)
            button.setFont(_icon_font(size, ratio))

        for button in self.findChildren(QToolButton):
            name = button.objectName()
            if name == "prototypeTopButton":
                _apply(button, metrics.toolbar_button, 0.60)
            elif name == "prototypeViewModeButton":
                _apply(button, max(24, metrics.toolbar_button - 6), 0.62)
            elif name == "prototypeRailButton":
                _apply(button, metrics.rail_button, 0.60)
            elif name == "prototypeActionButton":
                _apply(button, metrics.review_button, 0.58)
            elif name == "prototypeSmallButton":
                _apply(button, max(30, min(metrics.rail_button, metrics.review_button)), 0.58)
            elif name == "prototypeFlatIcon":
                _apply(button, metrics.review_button, 0.60)
            elif name == "prototypeBottomBarButton":
                _apply(button, max(28, metrics.rail_button - 3), 0.66)
            elif name == "prototypeSwatch":
                swatch = max(15, metrics.review_button // 2)
                button.setFixedSize(swatch, swatch)

    def _build_shell(self) -> QWidget:
        shell = QFrame()
        shell.setObjectName("prototypeShell")
        self._shell_layout = QVBoxLayout(shell)
        self._shell_layout.setContentsMargins(4, 4, 4, 4)
        self._shell_layout.setSpacing(0)
        self._top_bar = self._build_top_bar()
        self._shell_layout.addWidget(self._top_bar, 0)

        body = QWidget(shell)
        body.setObjectName("prototypeBody")
        self._body_layout = QHBoxLayout(body)
        self._body_layout.setContentsMargins(0, 4, 0, 0)
        self._body_layout.setSpacing(6)
        self._left_region = self._build_left_region()
        self._right_region = self._build_right_region()
        self._body_layout.addWidget(self._left_region, 0)
        self._body_layout.addWidget(self._build_center_region(), 1)
        self._body_layout.addWidget(self._right_region, 0)
        self._shell_layout.addWidget(body, 1)
        return shell

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("prototypeTopBar")
        self._top_bar_layout = QHBoxLayout(bar)
        self._top_bar_layout.setContentsMargins(10, 6, 10, 6)
        self._top_bar_layout.setSpacing(28)

        self._top_bar_layout.addWidget(
            self._button_group(
                ("☰", "▰", "←", "→", "↑", "⟳"),
                ("Menu", "Open folder", "Back", "Forward", "Up directory", "Refresh"),
                spacing=10,
            )
        )
        # Center of the top bar is intentionally left empty; the real toolbar
        # buttons from the backend will be placed here later.
        self._top_bar_layout.addStretch(1)
        self._top_bar_layout.addWidget(self._search_zoom_cluster())
        self._top_bar_layout.addWidget(self._button_group(("◫", "◧"), ("Left panels", "Right panels"), spacing=4))
        return bar

    def _build_left_region(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName("prototypeLeftRegion")
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._left_rail = self._build_left_rail()
        layout.addWidget(self._left_rail, 0)

        panel = QFrame()
        panel.setObjectName("prototypeLeftPanel")
        self._left_panel_layout = QVBoxLayout(panel)
        self._left_panel_layout.setContentsMargins(8, 0, 8, 0)
        self._left_panel_layout.setSpacing(6)
        # Two distinct floating panes over the dark background (plus the
        # standalone settings bar), mirroring the right-hand cards.
        self._folder_tree = self._folder_tree_visual()
        self._left_tabs_card = self._lower_left_tabs()
        self._left_bottom = self._left_bottom_bar()
        self._left_panel_layout.addWidget(self._folder_tree, 1)
        self._left_panel_layout.addWidget(self._left_tabs_card, 0)
        self._left_panel_layout.addWidget(self._left_bottom, 0)
        layout.addWidget(panel, 1)
        return wrapper

    def _build_left_rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("prototypeLeftRail")
        self._rail_layout = QVBoxLayout(rail)
        self._rail_layout.setContentsMargins(7, 9, 7, 9)
        self._rail_layout.setSpacing(10)
        for text, tip in (("⌂", "Home"), ("✚", "Import"), ("⌘", "Workflows"), ("▣", "Collections")):
            self._rail_layout.addWidget(self._tool_button(text, tip, "prototypeRailButton"), 0, Qt.AlignmentFlag.AlignHCenter)
        self._rail_layout.addStretch(1)
        return rail

    def _left_bottom_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("prototypeLeftBottomBar")
        self._bottom_bar_layout = QHBoxLayout(bar)
        self._bottom_bar_layout.setContentsMargins(8, 7, 8, 7)
        self._bottom_bar_layout.setSpacing(14)
        for text, tip in (("▣", "Collections"), ("◆", "Tags"), ("◒", "Activity")):
            self._bottom_bar_layout.addWidget(self._tool_button(text, tip, "prototypeBottomBarButton"), 0)
        self._bottom_bar_layout.addStretch(1)
        self._bottom_bar_layout.addWidget(self._tool_button("?", "Help", "prototypeBottomBarButton"), 0)
        self._bottom_bar_layout.addWidget(self._tool_button("⚙", "Settings", "prototypeBottomBarButton"), 0)
        return bar

    def _folder_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 2, 0, 4)
        layout.setSpacing(10)
        folder_icon = QLabel()
        folder_icon.setObjectName("prototypeFolderIcon")
        folder_icon.setPixmap(_folder_icon_pixmap(18))
        title = QLabel("Folders")
        title.setObjectName("prototypePanelTitle")
        layout.addWidget(folder_icon)
        layout.addWidget(title, 1)
        layout.addWidget(self._tool_button("⟳", "Refresh folders", "prototypeSmallButton"))
        return header

    def _folder_tree_visual(self) -> QWidget:
        tree = QFrame()
        tree.setObjectName("prototypeLeftCardTop")
        self._folder_tree_layout = QVBoxLayout(tree)
        self._folder_tree_layout.setContentsMargins(8, 8, 8, 8)
        self._folder_tree_layout.setSpacing(5)
        self._folder_tree_layout.addWidget(self._folder_header())
        folder_pixmap = _folder_icon_pixmap(16)
        names = _folder_names(self._owner)
        for index, name in enumerate(names):
            row = QWidget()
            row.setObjectName("prototypeFolderRowActive" if index == min(10, len(names) - 1) else "prototypeFolderRow")
            row.setProperty("prototypeFolderRow", True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(4 + (index % 3) * 10, 2, 4, 2)
            row_layout.setSpacing(6)
            row_layout.addWidget(QLabel("▸" if index % 4 else "▾"), 0)
            folder = QLabel()
            folder.setObjectName("prototypeFolderGlyph")
            folder.setPixmap(folder_pixmap)
            row_layout.addWidget(folder, 0)
            label = QLabel(name)
            label.setObjectName("prototypeTreeText")
            row_layout.addWidget(label, 1)
            if index in (2, 6, 10):
                dot = QLabel("●")
                dot.setObjectName(("prototypeDotGreen", "prototypeDotAmber", "prototypeDotRed")[index % 3])
                row_layout.addWidget(dot, 0)
            self._folder_tree_layout.addWidget(row, 0)
        self._folder_tree_layout.addStretch(1)
        return tree

    def _lower_left_tabs(self) -> QWidget:
        card = QFrame()
        card.setObjectName("prototypeLeftCardBottom")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 6, 10, 8)
        card_layout.setSpacing(0)
        tabs = QTabWidget()
        tabs.setObjectName("prototypeLeftTabs")
        tabs.addTab(self._review_controls_panel(), "Review Controls")
        tabs.addTab(self._activity_panel(), "AI / Activity")
        tabs.setUsesScrollButtons(False)
        tabs.setElideMode(Qt.TextElideMode.ElideRight)
        tabs.setFixedHeight(285)
        self._left_tabs = tabs
        card_layout.addWidget(tabs)
        return card

    def _review_controls_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("prototypeReviewPanel")
        self._review_layout = QVBoxLayout(panel)
        self._review_layout.setContentsMargins(8, 8, 8, 8)
        self._review_layout.setSpacing(9)
        self._review_layout.addWidget(self._color_strip(full_width=True))
        self._review_layout.addWidget(self._filter_rows())
        self._review_layout.addStretch(1)
        command_row = QHBoxLayout()
        command_row.setSpacing(6)
        for text, tip in (("✓", "Winner"), ("×", "Reject"), ("↗", "Move"), ("⌫", "Delete")):
            command_row.addWidget(self._tool_button(text, tip, "prototypeActionButton"))
        self._review_command_row = command_row
        self._review_layout.addLayout(command_row)
        return panel

    def _activity_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("prototypeActivityPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        for title, value, accent in (
            ("AI cache", "Idle", "#9da3aa"),
            ("Current folder", f"{len(self._items)} visual items", "#35d078"),
            ("Workflow", "Manual review", "#f0ad36"),
            ("Adapter", "Ready for labels", "#6b7cff"),
        ):
            row = QWidget()
            row.setObjectName("prototypeActivityRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 5, 8, 5)
            marker = QLabel("■")
            marker.setStyleSheet(f"color: {accent};")
            label = QLabel(title)
            label.setObjectName("prototypeMetaKey")
            state = QLabel(value)
            state.setObjectName("prototypeMetaValue")
            row_layout.addWidget(marker)
            row_layout.addWidget(label, 1)
            row_layout.addWidget(state)
            layout.addWidget(row)
        layout.addStretch(1)
        return panel

    def _build_center_region(self) -> QWidget:
        center = QWidget()
        center.setObjectName("prototypeCenter")
        layout = QVBoxLayout(center)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("prototypeGridScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        self._thumbnail_wall = PrototypeThumbnailWall(self._items, scroll)
        scroll.setWidget(self._thumbnail_wall)
        layout.addWidget(scroll, 1)
        return center

    def _build_right_region(self) -> QWidget:
        right = QFrame()
        right.setObjectName("prototypeRightPanel")
        self._right_layout = QVBoxLayout(right)
        self._right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_layout.setSpacing(4)
        # Distinct rounded cards floating over the dark viewport background.
        self._right_layout.addWidget(
            self._right_card(
                (
                    self._preview_panel(),
                    self._right_action_panel(),
                    self._metadata_block(),
                )
            ),
            0,
        )
        self._right_layout.addWidget(self._right_card((self._slider_block(),)), 0)
        self._right_layout.addWidget(self._right_card((self._toggle_block(),)), 0)
        blank = QFrame()
        blank.setObjectName("prototypeRightCard")
        blank.setMinimumHeight(0)
        blank.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._right_layout.addWidget(blank, 1)
        return right

    def _right_card(self, panes: tuple[QWidget, ...]) -> QFrame:
        card = QFrame()
        card.setObjectName("prototypeRightCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for pane in panes:
            layout.addWidget(pane)
        return card

    def _right_panel_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("prototypeRightPanelHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for text, tip in (("⊞", "Pop out"), ("⌖", "Pin location")):
            layout.addWidget(self._tool_button(text, tip, "prototypeSmallButton"))
        layout.addStretch(1)
        layout.addWidget(self._tool_button("×", "Close prototype", "prototypeSmallButton"))
        return header

    def _preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("prototypePreviewPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 10)
        layout.setSpacing(4)
        layout.addWidget(self._right_panel_header(), 0)
        layout.addSpacing(6)
        self._preview_image = QLabel()
        self._preview_image.setObjectName("prototypePreviewImage")
        self._preview_image.setMinimumHeight(230)
        self._preview_image.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = self._first_pixmap()
        self._preview_source_pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        if pixmap is not None and not pixmap.isNull():
            self._preview_image.setPixmap(pixmap.scaled(330, 248, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        else:
            self._preview_image.setPixmap(_large_placeholder_pixmap(QSize(350, 255)))
        layout.addWidget(self._preview_image)
        return panel

    def _right_action_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("prototypeRightActions")
        icon_row = QHBoxLayout(panel)
        icon_row.setContentsMargins(10, 5, 10, 5)
        icon_row.setSpacing(14)
        for text in ("ⓘ", "⌖", "◆", "▾", "▱", "◉"):
            icon_row.addWidget(self._tool_button(text, text, "prototypeFlatIcon"))
        icon_row.addStretch(1)
        self._right_action_layout = icon_row
        return panel

    def _metadata_block(self) -> QWidget:
        block = QFrame()
        block.setObjectName("prototypeInspectorBlock")
        block.setProperty("divided", True)
        layout = QGridLayout(block)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(6)
        rows = (
            ("File", self._items[0].name if self._items else "sample_001.nef"),
            ("Decision", "Winner" if self._items and self._items[0].accepted else "Unreviewed"),
            ("AI Pick", "Review"),
            ("Confidence", "86%"),
        )
        for row, (key, value) in enumerate(rows):
            key_label = QLabel(key)
            key_label.setObjectName("prototypeMetaKey")
            value_label = QLabel(value)
            value_label.setObjectName("prototypeMetaValue")
            layout.addWidget(key_label, row, 0)
            layout.addWidget(value_label, row, 1)
        return block

    def _slider_block(self) -> QWidget:
        block = QFrame()
        block.setObjectName("prototypeInspectorBlock")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        for title, value in (("Sharpness", 68), ("Exposure", 54)):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            label = QLabel(title)
            label.setObjectName("prototypeMetaKey")
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setObjectName("prototypeSlider")
            slider.setRange(0, 100)
            slider.setValue(value)
            slider.setEnabled(False)
            row_layout.addWidget(label)
            row_layout.addWidget(slider, 1)
            layout.addWidget(row)
        return block

    def _toggle_block(self) -> QWidget:
        block = QFrame()
        block.setObjectName("prototypeInspectorBlock")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(7)
        for label, color, checked in (
            ("Winner", "#35d078", True),
            ("Rejected", "#ff544d", False),
            ("Needs Review", "#f4b13e", True),
            ("AI Top Pick", "#69e34f", False),
            ("Color Label", "#9a4dff", True),
        ):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            marker = QLabel("■")
            marker.setStyleSheet(f"color: {color};")
            text = QLabel(label)
            text.setObjectName("prototypeMetaValue")
            toggle = QLabel("●" if checked else "○")
            toggle.setObjectName("prototypeToggleText")
            row_layout.addWidget(marker)
            row_layout.addWidget(text, 1)
            row_layout.addWidget(toggle)
            layout.addWidget(row)
        return block

    def _filter_rows(self) -> QWidget:
        panel = QWidget()
        layout = QGridLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
        rows = (
            ("Winners", "#35d078", "Manual"),
            ("Rejected", "#ff544d", "Manual"),
            ("Unreviewed", "#9da3aa", "Queue"),
            ("AI Picks", "#f4b13e", "AI"),
            ("Edited", "#4c7dff", "File"),
        )
        for row, (name, color, value) in enumerate(rows):
            marker = QLabel("■")
            marker.setStyleSheet(f"color: {color};")
            text = QLabel(name)
            text.setObjectName("prototypeMetaValue")
            value_label = QLabel(value)
            value_label.setObjectName("prototypeMetaKey")
            layout.addWidget(marker, row, 0)
            layout.addWidget(text, row, 1)
            layout.addWidget(value_label, row, 2)
        layout.setColumnStretch(1, 1)
        return panel

    def _color_strip(self, *, full_width: bool = False) -> QWidget:
        strip = QFrame() if not full_width else QWidget()
        strip.setObjectName("prototypeColorStripBox" if not full_width else "prototypeColorStrip")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(10 if not full_width else 0, 0, 10 if not full_width else 0, 0)
        layout.setSpacing(10 if full_width else 12)
        for name, color in (
            ("Red", "#ff544d"),
            ("Amber", "#ffb32f"),
            ("Lime", "#9fe32d"),
            ("Green", "#35d078"),
            ("Blue", "#3868ff"),
            ("Purple", "#9a4dff"),
        ):
            swatch = QToolButton()
            swatch.setObjectName("prototypeSwatch")
            swatch.setToolTip(f"{name} label")
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(f"QToolButton#prototypeSwatch {{ background: {color}; border: 1px solid #24272a; border-radius: 2px; }}")
            swatch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            layout.addWidget(swatch)
        if full_width:
            layout.addStretch(1)
        return strip

    def _search_zoom_cluster(self) -> QWidget:
        group = QWidget()
        group.setObjectName("prototypeZoomCluster")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        small_glass = QLabel("⌕")
        small_glass.setObjectName("prototypeZoomIconSmall")
        layout.addWidget(small_glass)
        self._zoom_line = QFrame()
        self._zoom_line.setObjectName("prototypeZoomLine")
        self._zoom_line.setFixedSize(120, 2)
        layout.addWidget(self._zoom_line)
        knob = QLabel("●")
        knob.setObjectName("prototypeZoomKnob")
        layout.addWidget(knob)
        large_glass = QLabel("⌕")
        large_glass.setObjectName("prototypeZoomIconLarge")
        layout.addWidget(large_glass)
        return group

    def _view_mode_group(self) -> QWidget:
        # Connected segmented control: one bordered container with flat buttons
        # joined by thin divider lines.
        group = QFrame()
        group.setObjectName("prototypeViewModeGroup")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(0)
        for index, (text, tooltip) in enumerate((("⌙", "Select"), ("▦", "Grid"), ("▤", "Rows"), ("▥", "Filmstrip"))):
            if index:
                divider = QFrame()
                divider.setObjectName("prototypeSegDivider")
                divider.setFixedWidth(1)
                layout.addWidget(divider)
            button = self._tool_button(text, tooltip, "prototypeViewModeButton")
            button.setProperty("active", index == 1)
            layout.addWidget(button)
        return group

    def _button_group(self, texts: tuple[str, ...], tooltips: tuple[str, ...], *, spacing: int = 6) -> QWidget:
        group = QWidget()
        group.setObjectName("prototypeLooseButtonGroup")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        for text, tooltip in zip(texts, tooltips, strict=False):
            layout.addWidget(self._tool_button(text, tooltip, "prototypeTopButton"))
        return group

    def _tool_button(self, text: str, tooltip: str, object_name: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName(object_name)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAutoRaise(True)
        top_like = object_name in {"prototypeTopButton", "prototypeViewModeButton"}
        button.setFixedSize(36 if top_like else 28, 32 if top_like else 28)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return button

    def _text_button(self, text: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("prototypeTextButton")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAutoRaise(True)
        return button

    def _first_pixmap(self) -> QPixmap | None:
        for item in self._items:
            if item.pixmap is not None and not item.pixmap.isNull():
                return item.pixmap
        return None


def open_generated_ui_prototype(owner: object | None) -> UIPrototypeWindow:
    existing = getattr(owner, "_ui_prototype_window", None)
    if isinstance(existing, UIPrototypeWindow):
        existing.raise_()
        existing.activateWindow()
        return existing
    window = UIPrototypeWindow(owner)
    if owner is not None:
        setattr(owner, "_ui_prototype_window", window)
    window.show()
    return window


def collect_prototype_items(owner: object | None, *, limit: int = 42) -> list[PrototypeItem]:
    records: list[ImageRecord] = list(getattr(owner, "_records", ()) or ())[:limit]
    annotations: dict[str, SessionAnnotation] = dict(getattr(owner, "_annotations", {}) or {})
    grid = getattr(owner, "grid", None)
    selected_indexes = set()
    if grid is not None:
        selected_indexes = set(getattr(grid, "_selected_indexes", set()) or set())
    items: list[PrototypeItem] = []
    for index, record in enumerate(records):
        annotation = annotations.get(record.path)
        pixmap = None
        if grid is not None:
            try:
                image = grid.thumbnail_for(index)
            except Exception:
                image = None
            if image is not None and not image.isNull():
                pixmap = QPixmap.fromImage(image)
        items.append(
            PrototypeItem(
                name=getattr(record, "name", Path(getattr(record, "path", f"image_{index + 1:03d}.jpg")).name),
                selected=index in selected_indexes or index == 0,
                accepted=bool(getattr(annotation, "winner", False)),
                rejected=bool(getattr(annotation, "reject", False)),
                color=_status_color(index, annotation),
                pixmap=pixmap,
            )
        )
    if items:
        return items
    return _placeholder_items(limit)


def _placeholder_items(limit: int) -> list[PrototypeItem]:
    return [
        PrototypeItem(
            name=f"landscape_{index + 1:03d}.jpg",
            selected=index in {0, 1, 5},
            accepted=index % 7 in {0, 1},
            rejected=index % 11 == 3,
            color=_palette_color(index),
        )
        for index in range(limit)
    ]


def _folder_names(owner: object | None) -> list[str]:
    folder = str(getattr(owner, "folder", "") or "").strip()
    if folder:
        root = Path(folder)
        names = [root.name or str(root)]
        try:
            names.extend(path.name for path in sorted(root.iterdir()) if path.is_dir())
        except OSError:
            pass
        if len(names) >= 8:
            return names[:18]
    return [
        "OneDrive",
        "Pictures",
        "Client Selects",
        "Mountain Set",
        "Coastline",
        "Imports",
        "2026",
        "June",
        "RAW",
        "Edited",
        "AI Review",
        "Exports",
        "Keepers",
        "Rejects",
        "Archive",
    ]


def _status_color(index: int, annotation: object | None) -> QColor:
    if bool(getattr(annotation, "reject", False)):
        return QColor("#ff544d")
    if bool(getattr(annotation, "winner", False)):
        return QColor("#35d078")
    return _palette_color(index)


def _palette_color(index: int) -> QColor:
    colors = ("#35d078", "#ffb32f", "#ff544d", "#9fe32d", "#3868ff", "#9a4dff")
    return QColor(colors[index % len(colors)])


def _folder_icon_pixmap(size: int = 16) -> QPixmap:
    """Plain flat folder icon (shared with the live window via prototype_style)."""
    return folder_icon_pixmap(size)


def _large_placeholder_pixmap(size: QSize) -> QPixmap:
    pixmap = QPixmap(size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRect(0, 0, size.width(), size.height())
    gradient = QLinearGradient(QPointF(rect.left(), rect.top()), QPointF(rect.right(), rect.bottom()))
    gradient.setColorAt(0.0, QColor("#ded2bf"))
    gradient.setColorAt(0.45, QColor("#9c8065"))
    gradient.setColorAt(1.0, QColor("#1a1512"))
    painter.fillRect(rect, gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#5b3c24"))
    cliff = QPainterPath()
    cliff.moveTo(rect.right(), rect.top() + 30)
    cliff.lineTo(rect.right() - 80, rect.top() + 64)
    cliff.lineTo(rect.right() - 130, rect.bottom() - 70)
    cliff.lineTo(rect.right() - 34, rect.bottom())
    cliff.lineTo(rect.right(), rect.bottom())
    cliff.closeSubpath()
    painter.drawPath(cliff)
    painter.setBrush(QColor("#101214"))
    painter.drawEllipse(QPointF(rect.width() * 0.48, rect.height() * 0.62), 20, 52)
    painter.end()
    return pixmap


def _prototype_stylesheet() -> str:
    return """
        QMainWindow#generatedUIPrototypeWindow {
            background: #050505;
        }
        QFrame#prototypeShell {
            background: #070707;
            border: 2px solid #030303;
            border-radius: 13px;
        }
        QWidget#prototypeTopBar, QFrame#prototypeContextBar {
            background: #141415;
            border: 1px solid #252628;
            border-radius: 10px;
        }
        QWidget#prototypeBody, QWidget#prototypeCenter {
            background: #070707;
        }
        QFrame#prototypeToolbarGroup {
            background: #20201f;
            border: 1px solid #2d2f32;
            border-radius: 7px;
        }
        QFrame#prototypeColorStripBox {
            background: transparent;
            border: none;
            border-radius: 7px;
        }
        QToolButton#prototypeTopButton, QToolButton#prototypeSmallButton {
            background: #20201f;
            border: 1px solid #2b2d30;
            border-radius: 7px;
            color: #bfc4ca;
        }
        QFrame#prototypeViewModeGroup {
            background: #20201f;
            border: 1px solid #2b2d30;
            border-radius: 7px;
        }
        QFrame#prototypeSegDivider {
            background: #34352f;
            border: none;
        }
        QToolButton#prototypeViewModeButton {
            background: transparent;
            border: none;
            border-radius: 5px;
            color: #bfc4ca;
        }
        QToolButton#prototypeFlatIcon {
            background: #20201f;
            border: 1px solid #2b2d30;
            border-radius: 7px;
            color: #bfc4ca;
        }
        QToolButton#prototypeActionButton {
            background: #20201f;
            border: 1px solid #252628;
            border-radius: 7px;
            color: #bfc4ca;
        }
        QToolButton#prototypeRailButton {
            background: transparent;
            border: none;
            border-radius: 7px;
            color: #bfc4ca;
        }
        QToolButton#prototypeTopButton:hover, QToolButton#prototypeSmallButton:hover,
        QToolButton#prototypeViewModeButton:hover {
            background: #313130;
            border-color: #3a3d41;
            color: #f1f3f5;
        }
        QToolButton#prototypeRailButton:hover {
            background: #181818;
            border: none;
            color: #f1f3f5;
        }
        QToolButton#prototypeActionButton:hover {
            background: #313130;
            border-color: #3a3d41;
            color: #f1f3f5;
        }
        QToolButton#prototypeViewModeButton[active="true"] {
            background: #34352f;
            color: #eef1f4;
        }
        QWidget#prototypeLooseButtonGroup QToolButton#prototypeTopButton:pressed {
            background: #2c3035;
        }
        QToolButton#prototypeFlatIcon {
            background: transparent;
            border-color: transparent;
            color: #8f949a;
        }
        QToolButton#prototypeTextButton {
            background: #191b1d;
            border: 1px solid #25282b;
            border-radius: 4px;
            color: #c7ccd2;
            padding: 4px 10px;
            font: 11px "Segoe UI";
        }
        QFrame#prototypeLeftRail {
            background: #0d0d0d;
            border: 1px solid #242527;
            border-top-left-radius: 10px;
            border-bottom-left-radius: 10px;
            border-top-right-radius: 0px;
            border-bottom-right-radius: 0px;
        }
        QFrame#prototypeLeftBottomBar {
            background: #161615;
            border: 1px solid #242527;
            border-left: none;
            border-top-left-radius: 0px;
            border-top-right-radius: 0px;
            border-bottom-left-radius: 0px;
            border-bottom-right-radius: 10px;
        }
        QToolButton#prototypeBottomBarButton {
            background: transparent;
            border: none;
            color: #8d939a;
        }
        QToolButton#prototypeBottomBarButton:hover {
            color: #dfe3e7;
        }
        QFrame#prototypeLeftPanel {
            background: transparent;
            border: none;
        }
        QFrame#prototypeRightPanel {
            background: transparent;
            border: none;
        }
        QFrame#prototypeRightCard {
            background: #151515;
            border: 1px solid #242527;
            border-radius: 10px;
        }
        QWidget#prototypeLeftRegion {
        }
        QLabel#prototypePanelTitle {
            color: #d7dce1;
            font: 600 13px "Segoe UI";
        }
        QLabel#prototypeFolderIcon, QLabel#prototypeFolderGlyph {
            color: #d6bc65;
        }
        QFrame#prototypeLeftCardTop {
            background: #151515;
            border: 1px solid #242527;
            border-left: none;
            border-bottom: none;
            border-top-left-radius: 0px;
            border-top-right-radius: 10px;
            border-bottom-left-radius: 0px;
            border-bottom-right-radius: 0px;
        }
        QFrame#prototypeLeftCardBottom {
            background: #111111;
            border-left: none;
            border-right: 1px solid #242527;
            border-top: 1px solid #242527;
            border-bottom: none;
            border-radius: 0px;
        }
        QWidget#prototypeFolderRow, QWidget#prototypeFolderRowActive {
            min-height: 20px;
            max-height: 22px;
            border-radius: 6px;
        }
        QWidget#prototypeFolderRowActive {
            background: #282828;
        }
        QLabel#prototypeTreeText, QLabel#prototypeMetaValue {
            color: #bcc2c8;
            font: 12px "Segoe UI";
        }
        QLabel#prototypeMetaKey {
            color: #747a82;
            font: 11px "Segoe UI";
        }
        QLabel#prototypeDotGreen { color: #35d078; }
        QLabel#prototypeDotAmber { color: #ffb32f; }
        QLabel#prototypeDotRed { color: #ff544d; }
        QTabWidget#prototypeLeftTabs::pane {
            background: transparent;
            border: none;
            border-top: 1px solid #242527;
            top: -1px;
        }
        QWidget#prototypeReviewPanel, QWidget#prototypeActivityPanel {
            background: transparent;
        }
        QTabBar::tab {
            background: transparent;
            border: none;
            border-bottom: 2px solid transparent;
            color: #858b93;
            padding: 6px 12px;
            margin-right: 6px;
            font: 12px "Segoe UI";
        }
        QTabBar::tab:selected {
            color: #e6e9ec;
            border-bottom: 2px solid #7d838b;
        }
        QTabBar::tab:hover:!selected {
            color: #c2c7cd;
        }
        QFrame#prototypeActivityRow {
            background: #161516;
            border: 1px solid #202020;
            border-radius: 5px;
        }
        QFrame#prototypeInspectorBlock,
        QFrame#prototypeRightActions,
        QFrame#prototypePreviewPanel {
            background: transparent;
            border: none;
            border-radius: 0px;
        }
        QFrame#prototypeRightActions,
        QFrame#prototypeInspectorBlock[divided="true"] {
            border-top: 1px solid #1f1f20;
        }
        QLabel#prototypeSecondaryText {
            color: #838991;
            font: 11px "Segoe UI";
        }
        QScrollArea#prototypeGridScroll, QWidget#prototypeThumbnailWall {
            background: #070707;
            border: none;
        }
        QWidget#prototypeInspectorBody {
            background: transparent;
            border: none;
        }
        QLabel#prototypePreviewImage {
            background: #151515;
            border: none;
            border-radius: 6px;
        }
        QFrame#prototypeZoomLine {
            background: #64686d;
            border: none;
        }
        QLabel#prototypeZoomKnob {
            color: #aeb3b9;
            margin-left: -74px;
        }
        QWidget#prototypeZoomCluster QLabel {
            color: #b7bcc2;
            font: 14px "Segoe UI Symbol";
        }
        QWidget#prototypeZoomCluster QLabel#prototypeZoomIconSmall {
            color: #b7bcc2;
            font: 18px "Segoe UI Symbol";
        }
        QWidget#prototypeZoomCluster QLabel#prototypeZoomIconLarge {
            color: #b7bcc2;
            font: 25px "Segoe UI Symbol";
        }
        QLabel#prototypeToggleText {
            color: #90969d;
            font: 15px "Segoe UI Symbol";
        }
        QSlider#prototypeSlider::groove:horizontal {
            height: 2px;
            background: #45494e;
        }
        QSlider#prototypeSlider::handle:horizontal {
            width: 10px;
            height: 10px;
            margin: -4px 0;
            border-radius: 5px;
            background: #bec3c9;
        }
        QScrollBar:vertical {
            background: #070707;
            width: 10px;
        }
        QScrollBar::handle:vertical {
            background: #2a2d31;
            border-radius: 4px;
            min-height: 32px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
    """
