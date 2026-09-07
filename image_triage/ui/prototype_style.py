"""Shared presentation primitives extracted from the UI prototype.

These are the reusable, behaviour-free pieces of the generated prototype that
the real application window adopts during the prototype-to-app migration: the
exact colour tokens the design was tuned around, and the custom-drawn folder
icon. Keeping them in one module avoids duplicating the design between the
standalone prototype (`generated_prototype.py`) and the live `MainWindow`.
"""

from __future__ import annotations

import time

from PySide6.QtCore import (
    QFileInfo,
    QModelIndex,
    QPointF,
    QRect,
    QRectF,
    QSize,
    QStorageInfo,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QFileIconProvider,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionTab,
    QStyleOptionViewItem,
    QStylePainter,
    QTabBar,
    QTreeView,
    QWidget,
)


# --- Prototype colour tokens -------------------------------------------------
# The hex values the prototype layout was approved against. The real window's
# Dark/Midnight palettes are tuned toward these during the migration.
PROTO_RAIL_BG = "#0d0d0d"          # left vertical button rail
PROTO_DIRECTORY_BG = "#161516"     # directory / folder panel
PROTO_FOLDER_CARD_BG = "#151515"   # folder pane card
PROTO_REVIEW_CARD_BG = "#111111"   # review / AI activity card
PROTO_VIEWPORT_BG = "#070707"      # image viewport background
PROTO_RATING_FOOTER_BG = "#141313"  # metadata strip under each thumbnail
PROTO_RIGHT_CARD_BG = "#151515"    # right inspector cards
PROTO_TOPBAR_BG = "#141415"        # top bar
PROTO_BUTTON_BG = "#20201f"        # top-bar button background
PROTO_BUTTON_HOVER = "#313130"     # top-bar button hover
PROTO_RAIL_BUTTON_HOVER = "#181818"  # rail button hover
PROTO_SETTINGS_BAR_BG = "#161615"  # bottom settings bar
PROTO_DIVIDER = "#242527"          # connected-pane definition lines
PROTO_CARD_RADIUS = 10

PROTO_FOLDER_COLOR = "#d3b15b"     # flat folder icon gold
PROTO_DRIVE_COLOR = "#8f9bb0"      # flat drive icon steel
PROTO_DRIVE_LED_COLOR = "#5ad17e"  # drive activity LED accent
SIDEBAR_ACCENT_COLOR = "#579bff"


class CompactIconTabBar(QTabBar):
    """Paint icon-and-label tabs with an explicit, predictable gap."""

    def __init__(self, parent: QWidget | None = None, *, icon_text_gap: int = 2) -> None:
        super().__init__(parent)
        self._icon_text_gap = max(0, int(icon_text_gap))
        self._normal_text_color = QColor("#8390a2")
        self._hover_text_color = QColor("#b8c2d0")
        self._selected_text_color = QColor("#f3f6fb")

    def set_text_colors(
        self,
        normal: QColor,
        selected: QColor,
        hover: QColor | None = None,
    ) -> None:
        self._normal_text_color = QColor(normal)
        self._selected_text_color = QColor(selected)
        self._hover_text_color = QColor(hover or selected)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QStylePainter(self)
        painter.setClipRect(event.rect())
        selected_index = self.currentIndex()
        order = [index for index in range(self.count()) if index != selected_index]
        if selected_index >= 0:
            order.append(selected_index)

        for index in order:
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            icon = QIcon(option.icon)
            text = option.text

            # Let the active Qt stylesheet retain ownership of the tab shape,
            # underline, hover background, and borders. Only its label layout
            # is replaced because Qt does not expose that icon/text gap.
            option.icon = QIcon()
            option.text = ""
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabShape, option)

            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
            font = QFont(self.font())
            font.setPixelSize(14)
            font.setWeight(QFont.Weight.Bold if selected else QFont.Weight.DemiBold)
            painter.setFont(font)
            if selected:
                painter.setPen(self._selected_text_color)
            elif hovered:
                painter.setPen(self._hover_text_color)
            else:
                painter.setPen(self._normal_text_color)

            content_rect = option.rect.adjusted(10, 0, -10, 0)
            icon_size = self.iconSize()
            maximum_text_width = max(
                0,
                content_rect.width() - icon_size.width() - self._icon_text_gap,
            )
            metrics = painter.fontMetrics()
            display_text = metrics.elidedText(
                text,
                Qt.TextElideMode.ElideRight,
                maximum_text_width,
            )
            text_width = metrics.horizontalAdvance(display_text)
            content_width = icon_size.width() + self._icon_text_gap + text_width
            left = content_rect.x() + max(0, (content_rect.width() - content_width) // 2)
            icon_rect = QRect(
                left,
                option.rect.center().y() - icon_size.height() // 2,
                icon_size.width(),
                icon_size.height(),
            )
            mode = (
                QIcon.Mode.Normal
                if option.state & QStyle.StateFlag.State_Enabled
                else QIcon.Mode.Disabled
            )
            state = QIcon.State.On if selected else QIcon.State.Off
            icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter, mode, state)
            text_rect = QRect(
                icon_rect.x() + icon_rect.width() + self._icon_text_gap,
                option.rect.y(),
                text_width,
                option.rect.height(),
            )
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                display_text,
            )


def folder_icon_pixmap(size: int = 16, color: str = PROTO_FOLDER_COLOR) -> QPixmap:
    """A plain, flat single-tone folder icon with the classic angled tab.

    Rendered at 2x and tagged with a device pixel ratio so it stays crisp.
    """
    scale = 2
    s = size * scale
    pixmap = QPixmap(s, s)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    folder = QColor(color)
    path = QPainterPath()
    path.moveTo(s * 0.10, s * 0.80)
    path.lineTo(s * 0.10, s * 0.26)
    path.lineTo(s * 0.40, s * 0.26)
    path.lineTo(s * 0.49, s * 0.37)
    path.lineTo(s * 0.90, s * 0.37)
    path.lineTo(s * 0.90, s * 0.80)
    path.closeSubpath()
    pen = QPen(folder, s * 0.085)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(folder)
    painter.drawPath(path)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def sidebar_people_icon_pixmap(
    size: int = 20, color: str = SIDEBAR_ACCENT_COLOR
) -> QPixmap:
    """Filled two-person icon, with the left person in the foreground."""
    scale = 2
    s = size * scale
    pixmap = QPixmap(s, s)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    unit = s / 20.0
    rear = QColor(color)
    rear.setAlpha(205)
    painter.setBrush(rear)
    painter.drawEllipse(QPointF(13.7 * unit, 6.2 * unit), 2.7 * unit, 2.7 * unit)
    painter.drawRoundedRect(
        QRectF(10.0 * unit, 9.3 * unit, 8.0 * unit, 6.7 * unit),
        2.8 * unit,
        2.8 * unit,
    )

    painter.setBrush(QColor(color))
    painter.drawEllipse(QPointF(7.1 * unit, 5.4 * unit), 3.1 * unit, 3.1 * unit)
    painter.drawRoundedRect(
        QRectF(1.8 * unit, 8.8 * unit, 10.8 * unit, 7.6 * unit),
        3.4 * unit,
        3.4 * unit,
    )
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def sidebar_projects_icon_pixmap(
    size: int = 20, color: str = SIDEBAR_ACCENT_COLOR
) -> QPixmap:
    """Filled stacked-layers icon from the generated sidebar reference."""
    scale = 2
    s = size * scale
    pixmap = QPixmap(s, s)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    base = QColor(color)
    unit = s / 20.0
    for top, alpha in ((9.8, 170), (6.3, 215), (2.8, 255)):
        layer = QColor(base)
        layer.setAlpha(alpha)
        painter.setBrush(layer)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(10.0 * unit, top * unit),
                    QPointF(18.0 * unit, (top + 4.0) * unit),
                    QPointF(10.0 * unit, (top + 8.0) * unit),
                    QPointF(2.0 * unit, (top + 4.0) * unit),
                ]
            )
        )
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


class FolderTreeView(QTreeView):
    """Folder tree with compact custom rows and direct expand/collapse clicks."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setIndentation(22)
        self.setRootIsDecorated(False)
        self.setIconSize(QSize(20, 20))
        self.setUniformRowHeights(False)
        self.setItemDelegate(_FolderTreeDelegate(self))
        self._single_drive_expansion_enabled = True
        self._enforcing_single_expansion = False
        self.expanded.connect(self._handle_index_expanded)

    def single_drive_expansion_enabled(self) -> bool:
        return self._single_drive_expansion_enabled

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        position = event.position().toPoint()
        index = self.indexAt(position)
        item_rect = self.visualRect(index) if index.isValid() else QRect()
        clicked_disclosure = item_rect.isValid() and position.x() < item_rect.left()
        was_expanded = index.isValid() and self.isExpanded(index)
        model = self.model()
        expandable = bool(model is not None and index.isValid() and model.hasChildren(index))
        super().mousePressEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and expandable
            and not clicked_disclosure
        ):
            self.collapse(index) if was_expanded else self.expand(index)

    def set_single_drive_expansion_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._single_drive_expansion_enabled:
            if enabled:
                self._enforce_single_branch_expansion()
            return
        self._single_drive_expansion_enabled = enabled
        if enabled:
            self._enforce_single_branch_expansion()

    def _handle_index_expanded(self, index: QModelIndex) -> None:
        if (
            not self._single_drive_expansion_enabled
            or self._enforcing_single_expansion
        ):
            return
        self._collapse_expanded_siblings(index)

    def _enforce_single_branch_expansion(self) -> None:
        model = self.model()
        if model is None:
            return
        current_path: list[QModelIndex] = []
        current = self.currentIndex()
        while current.isValid():
            current_path.append(current)
            current = current.parent()

        parent = QModelIndex()
        while True:
            preferred = next(
                (candidate for candidate in current_path if candidate.parent() == parent),
                QModelIndex(),
            )
            expanded = [
                model.index(row, 0, parent)
                for row in range(model.rowCount(parent))
                if self.isExpanded(model.index(row, 0, parent))
            ]
            if not expanded:
                return
            keep = preferred if preferred in expanded else expanded[0]
            self._collapse_expanded_siblings(keep)
            parent = keep

    def _collapse_expanded_siblings(self, keep: QModelIndex) -> None:
        model = self.model()
        if model is None:
            return
        parent = keep.parent()
        self._enforcing_single_expansion = True
        try:
            for row in range(model.rowCount(parent)):
                candidate = model.index(row, 0, parent)
                if candidate != keep and self.isExpanded(candidate):
                    self.collapse(candidate)
        finally:
            self._enforcing_single_expansion = False

    def drawRow(self, painter, option, index) -> None:  # type: ignore[override]
        selection_model = self.selectionModel()
        selected = index == self.currentIndex() or bool(
            selection_model is not None and selection_model.isSelected(index)
        )
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            rect = QRect(option.rect)
            rect.setLeft(self.viewport().rect().left() + 1)
            rect = rect.adjusted(0, 1, -2, -1)
            fill = option.palette.color(QPalette.ColorRole.Highlight)
            fill.setAlpha(92 if selected else 48)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(rect), 6, 6)
            painter.restore()
        depth = 0
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()

        content_option = QStyleOptionViewItem(option)
        content_option.rect = QRect(option.rect)
        content_option.palette = self.palette()
        branch_width = depth * (self.indentation() or 16)
        if branch_width > 0:
            branch_rect = QRect(option.rect)
            branch_rect.setWidth(branch_width)
            self.drawBranches(painter, branch_rect, index)
            content_option.rect.setLeft(option.rect.left() + branch_width)
        self.itemDelegate().paint(painter, content_option, index)

    def drawBranches(self, painter: QPainter, rect, index) -> None:  # type: ignore[override]
        model = self.model()
        if model is None or not model.hasChildren(index):
            # Leaf rows get no branch decoration at all (no guide lines).
            return
        indent = self.indentation() or 16
        size = 3.0
        cx = rect.right() - indent / 2.0 + 0.5
        cy = rect.center().y() + 0.5
        color = QColor("#8a909a")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.isExpanded(index):
            points = QPolygonF(
                [
                    QPointF(cx - size, cy - size * 0.4),
                    QPointF(cx, cy + size * 0.6),
                    QPointF(cx + size, cy - size * 0.4),
                ]
            )
        else:
            points = QPolygonF(
                [
                    QPointF(cx - size * 0.4, cy - size),
                    QPointF(cx + size * 0.6, cy),
                    QPointF(cx - size * 0.4, cy + size),
                ]
            )
        painter.drawPolyline(points)
        painter.restore()


class _FolderTreeDelegate(QStyledItemDelegate):
    """Adds the target sidebar's drive meters and selected-row affordance."""

    DRIVE_ROW_HEIGHT = 36
    FOLDER_ROW_HEIGHT = 26
    _USAGE_CACHE_SECONDS = 30.0

    def __init__(self, tree: FolderTreeView) -> None:
        super().__init__(tree)
        self._tree = tree
        self._usage_cache: dict[str, tuple[float, float | None]] = {}

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        hint = super().sizeHint(option, index)
        height = self.DRIVE_ROW_HEIGHT if self._is_drive(index) else self.FOLDER_ROW_HEIGHT
        return QSize(max(0, hint.width()), height)

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        view_option = QStyleOptionViewItem(option)
        self.initStyleOption(view_option, index)
        if self._is_drive(index):
            self._paint_drive(painter, view_option, index)
            return
        self._paint_folder(painter, view_option, index)

    def _paint_drive(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        rect = option.rect.adjusted(1, 1, -2, -1)
        selected = self._is_selected(index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        icon_rect = QRect(
            rect.left() + 5,
            rect.top() + max(0, (rect.height() - 20) // 2),
            20,
            20,
        )
        if not option.icon.isNull():
            option.icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)

        text_left = icon_rect.right() + 9
        text_rect = QRect(text_left, rect.top(), max(0, rect.right() - text_left - 6), 20)
        color_role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        painter.setPen(option.palette.color(color_role))
        painter.setFont(option.font)
        label = option.fontMetrics.elidedText(
            option.text, Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)

        # The expanded drive is already identified by its open branch and gains
        # useful vertical room by omitting the capacity meter, as in the target.
        if not self._tree.isExpanded(index):
            ratio = self._drive_usage_ratio(index)
            if ratio is not None:
                bar = QRectF(text_left, rect.top() + 25, max(24, rect.right() - text_left - 7), 4)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(58, 66, 77, 210))
                painter.drawRoundedRect(bar, 2, 2)
                if ratio > 0:
                    used = QRectF(bar)
                    used.setWidth(max(5.0, bar.width() * ratio))
                    painter.setBrush(QColor("#5b9cff"))
                    painter.drawRoundedRect(used, 2, 2)
        painter.restore()

    def _paint_folder(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        rect = option.rect.adjusted(1, 1, -2, -1)
        selected = self._is_selected(index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        icon_size = min(20, max(0, rect.height() - 4))
        icon_rect = QRect(
            rect.left() + 5,
            rect.top() + max(0, (rect.height() - icon_size) // 2),
            icon_size,
            icon_size,
        )
        if not option.icon.isNull():
            option.icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)

        text_left = icon_rect.right() + 8
        trailing_space = 24 if selected else 7
        text_rect = QRect(
            text_left,
            rect.top(),
            max(0, rect.right() - text_left - trailing_space),
            rect.height(),
        )
        color_role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        painter.setPen(option.palette.color(color_role))
        painter.setFont(option.font)
        label = option.fontMetrics.elidedText(
            option.text, Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        if selected:
            self._paint_more_button(
                painter,
                rect,
                option.palette.color(QPalette.ColorRole.PlaceholderText),
            )
        painter.restore()

    def _paint_more_button(self, painter: QPainter, rect: QRect, color: QColor) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not color.isValid():
            color = QColor("#aab4c2")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        x = rect.right() - 8
        for y in (rect.center().y() - 4, rect.center().y(), rect.center().y() + 4):
            painter.drawEllipse(QPointF(x, y), 1.15, 1.15)
        painter.restore()

    def _is_drive(self, index) -> bool:
        return _index_is_drive(index)

    def _is_selected(self, index: QModelIndex) -> bool:
        selection_model = self._tree.selectionModel()
        return index == self._tree.currentIndex() or bool(
            selection_model is not None and selection_model.isSelected(index)
        )

    def _drive_usage_ratio(self, index) -> float | None:
        model = index.model()
        try:
            path = str(model.filePath(index))
        except (AttributeError, RuntimeError):
            return None
        now = time.monotonic()
        cached = self._usage_cache.get(path)
        if cached is not None and now - cached[0] < self._USAGE_CACHE_SECONDS:
            return cached[1]
        storage = QStorageInfo(path)
        total = int(storage.bytesTotal())
        available = int(storage.bytesAvailable())
        ratio = None if total <= 0 else max(0.0, min(1.0, (total - available) / total))
        self._usage_cache[path] = (now, ratio)
        return ratio


def _index_is_drive(index: QModelIndex) -> bool:
    if not index.isValid() or index.parent().isValid():
        return False
    model = index.model()
    try:
        return bool(model.fileInfo(index).isRoot())
    except (AttributeError, RuntimeError):
        return False


def drive_icon_pixmap(
    size: int = 16,
    color: str = PROTO_DRIVE_COLOR,
    led_color: str = PROTO_DRIVE_LED_COLOR,
) -> QPixmap:
    """A flat, single-tone external-drive icon with a small activity LED.

    Deliberately a different silhouette and colour from the folder icon so
    drive roots read as distinct from ordinary directories in the tree.
    """
    scale = 2
    s = size * scale
    pixmap = QPixmap(s, s)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    body = QColor(color)
    # Drive body: a landscape rounded rectangle.
    rect = QRectF(s * 0.12, s * 0.34, s * 0.76, s * 0.34)
    path = QPainterPath()
    path.addRoundedRect(rect, s * 0.07, s * 0.07)
    painter.fillPath(path, body)
    # A subtle separator slot near the top, carved darker for depth.
    slot = QColor(0, 0, 0, 60)
    slot_pen = QPen(slot, s * 0.03)
    painter.setPen(slot_pen)
    painter.drawLine(QPointF(s * 0.22, s * 0.43), QPointF(s * 0.78, s * 0.43))
    # Activity LED on the right side.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(led_color))
    painter.drawEllipse(QPointF(s * 0.74, s * 0.58), s * 0.035, s * 0.035)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


class PrototypeFileIconProvider(QFileIconProvider):
    """Supplies the flat prototype folder icon for directories in tree views."""

    def __init__(self, size: int = 18) -> None:
        super().__init__()
        self._folder_icon = QIcon(folder_icon_pixmap(size))
        self._drive_icon = QIcon(drive_icon_pixmap(size))

    def icon(self, info) -> QIcon:  # type: ignore[override]
        if isinstance(info, QFileInfo):
            if info.isDir():
                if self._is_drive(info):
                    return self._drive_icon
                return self._folder_icon
            return super().icon(info)
        if info == QFileIconProvider.IconType.Drive:
            return self._drive_icon
        if info == QFileIconProvider.IconType.Folder:
            return self._folder_icon
        return super().icon(info)

    @staticmethod
    def _is_drive(info: QFileInfo) -> bool:
        """True for drive/filesystem roots (e.g. ``C:\\`` or a UNC share root)."""
        if info.isRoot():
            return True
        path = info.absoluteFilePath()
        # Normalise so ``C:`` and ``C:/`` both register as drive roots.
        stripped = path.rstrip("/\\")
        if len(stripped) == 2 and stripped[1] == ":":
            return True
        return False
