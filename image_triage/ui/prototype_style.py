"""Shared presentation primitives extracted from the UI prototype.

These are the reusable, behaviour-free pieces of the generated prototype that
the real application window adopts during the prototype-to-app migration: the
exact colour tokens the design was tuned around, and the custom-drawn folder
icon. Keeping them in one module avoids duplicating the design between the
standalone prototype (`generated_prototype.py`) and the live `MainWindow`.
"""

from __future__ import annotations

from pathlib import Path
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
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QFileIconProvider,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
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

# How many drives the Drives list shows before it stops growing and scrolls.
MAX_VISIBLE_DRIVES = 5


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


# --- Supplied rail marks -----------------------------------------------------
# The rail's icons, as delivered artwork rather than drawn paths. Each is one
# mark centred in a 64px frame with its own margin, so they are trimmed to
# their ink before use and every mark lands at the same weight.
NAV_ICON_ASSETS = {
    "library": "nav_library.png",
    "faces": "nav_faces.png",
    "duplicates": "nav_duplicates.png",
    "groups": "nav_groups.png",
    "collections": "nav_collections.png",
}

# The rail's pinned tools, keyed by the toolbar item they run.
TOOL_ICON_ASSETS = {
    "command_palette": "tool_command_palette.png",
    "batch_rename": "tool_batch_rename.png",
    "batch_resize": "tool_batch_resize.png",
    "keyboard_shortcuts": "tool_keyboard_shortcuts.png",
}

_ASSET_DIR = Path(__file__).resolve().parent / "assets"
_icon_sources: dict[str, "QImage | None"] = {}
_icon_cache: dict[tuple[str, int, str, int], QPixmap] = {}


def _icon_source(name: str | None) -> "QImage | None":
    """The trimmed ink mask for artwork file ``name``, or None if not installed."""
    if not name:
        return None
    if name in _icon_sources:
        return _icon_sources[name]
    image = None
    path = _ASSET_DIR / name
    if path.is_file():
        loaded = QImage(str(path))
        if not loaded.isNull():
            image = _ink_mask(loaded)
    _icon_sources[name] = image
    return image


def _ink_mask(image: "QImage") -> "QImage | None":
    """A coverage mask for artwork drawn as light strokes, trimmed to its ink.

    The delivered marks keep their interior opaque and dark rather than clear,
    and they are rendered with subpixel antialiasing that tints every edge, so
    neither the alpha nor the colour is usable on its own. Coverage is taken
    from how light each pixel is, scaled by its alpha and normalised to the
    brightest stroke in the file, which drops the dark interior to nothing and
    keeps the stroke at full strength.
    """
    source = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = source.width(), source.height()
    coverage: list[list[int]] = []
    peak = 1
    for y in range(height):
        row = []
        for x in range(width):
            colour = source.pixelColor(x, y)
            value = max(colour.red(), colour.green(), colour.blue())
            ink = value * colour.alpha() // 255
            peak = max(peak, ink)
            row.append(ink)
        coverage.append(row)

    mask = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    mask.fill(Qt.GlobalColor.transparent)
    left, top = width, height
    right = bottom = -1
    for y in range(height):
        for x in range(width):
            ink = min(255, coverage[y][x] * 255 // peak)
            if ink <= 8:
                continue
            # Premultiplied white, so scaling below stays free of fringes.
            mask.setPixel(x, y, (ink << 24) | (ink << 16) | (ink << 8) | ink)
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x), max(bottom, y)
    if right < 0:
        return None
    return mask.copy(QRect(left, top, right - left + 1, bottom - top + 1))


def nav_icon_pixmap(icon_id: str, box: int, color: str, *, ratio: int = 2) -> QPixmap | None:
    """The supplied mark for a rail destination. See :func:`asset_icon_pixmap`."""
    return asset_icon_pixmap(NAV_ICON_ASSETS.get(icon_id), box, color, ratio=ratio)


def tool_icon_pixmap(item_id: str, box: int, color: str, *, ratio: int = 2) -> QPixmap | None:
    """The supplied mark for a pinned tool. See :func:`asset_icon_pixmap`."""
    return asset_icon_pixmap(TOOL_ICON_ASSETS.get(item_id), box, color, ratio=ratio)


def asset_icon_pixmap(name: str | None, box: int, color: str, *, ratio: int = 2) -> QPixmap | None:
    """A supplied mark, tinted ``color`` and fitted to a ``box``-pixel square.

    The mark is scaled by its longer side so it fills the box the way a glyph
    fills its em, then centred. Returns None when no artwork is installed under
    ``name``, leaving the caller to fall back to a drawn or glyph icon.
    """
    if not name:
        return None
    box = max(1, int(box))
    key = (name, box, color, ratio)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached
    source = _icon_source(name)
    if source is None:
        return None
    side = box * ratio
    scaled = source.scaled(
        side,
        side,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.drawImage(
        (side - scaled.width()) // 2, (side - scaled.height()) // 2, scaled
    )
    # Keep the mark's coverage, take its colour from the theme.
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(color))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    _icon_cache[key] = pixmap
    return pixmap


def library_icon_pixmap(size: int = 20, color: str = SIDEBAR_ACCENT_COLOR) -> QPixmap:
    """Three book spines, the last one leaning, as the rail's Library mark."""
    scale = 2
    s = size * scale
    pixmap = QPixmap(s, s)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    unit = s / 20.0
    # Outlined spines, as the design draws them: the stroke is centred on the
    # path, so every rectangle below is already inset by half its width.
    stroke = 1.8 * unit
    pen = QPen(QColor(color), stroke)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = 1.1 * unit
    painter.drawRoundedRect(
        QRectF(0.90 * unit, 2.40 * unit, 4.20 * unit, 15.70 * unit), radius, radius
    )
    painter.drawRoundedRect(
        QRectF(7.40 * unit, 2.40 * unit, 4.20 * unit, 15.70 * unit), radius, radius
    )
    # The third leans off the end of the shelf.
    painter.save()
    painter.translate(12.8 * unit, 19.0 * unit)
    painter.rotate(13.0)
    painter.drawRoundedRect(
        QRectF(0.90 * unit, -13.10 * unit, 2.40 * unit, 12.20 * unit), radius, radius
    )
    painter.restore()
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
        self._selected_row_fill = QColor(SIDEBAR_ACCENT_COLOR)
        self._selected_row_fill.setAlpha(58)
        self._hovered_row_fill = QColor(SIDEBAR_ACCENT_COLOR)
        self._hovered_row_fill.setAlpha(28)
        self._single_drive_expansion_enabled = True
        self._enforcing_single_expansion = False
        self._drives_only = False
        self._usage_track = QColor(58, 66, 77, 210)
        self._usage_fill = (QColor("#5b9cff"), QColor("#5b9cff"))
        self._drive_icon_provider = None
        self._drive_row_height: int | None = None
        self._drive_text_px: int | None = None
        # Usage-bar sizes; the window replaces these from layout_ratios.
        self._meter_height = 4
        self._meter_gap = 5
        self.expanded.connect(self._handle_index_expanded)

    def set_drives_only(self, enabled: bool) -> None:
        """Show just the top-level drives as a flat, fixed-height list."""
        self._drives_only = bool(enabled)
        self.setItemsExpandable(not self._drives_only)
        self.setExpandsOnDoubleClick(not self._drives_only)
        if self._drives_only:
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            # The vertical policy belongs to fit_height_to_rows, which knows
            # whether the list overflows the row cap.
            self.fit_height_to_rows()
        self.updateGeometry()

    def setRootIndex(self, index: QModelIndex) -> None:  # type: ignore[override]
        # Under a drive root the top-level folders need their own branch
        # column (for expand arrows); Qt only reserves it when decorated.
        super().setRootIndex(index)
        self.setRootIsDecorated(index.isValid())

    def drives_only(self) -> bool:
        return self._drives_only

    def set_usage_bar_colors(self, track: QColor, fill_start: QColor, fill_end: QColor) -> None:
        self._usage_track = QColor(track)
        self._usage_fill = (QColor(fill_start), QColor(fill_end))
        self.viewport().update()

    def usage_bar_colors(self) -> tuple[QColor, tuple[QColor, QColor]]:
        return self._usage_track, self._usage_fill

    def set_drive_row_height(self, height: int | None) -> None:
        """Pitch of the drive rows (name to name); None keeps the default."""
        self._drive_row_height = int(height) if height else None
        self.scheduleDelayedItemsLayout()
        if self._drives_only:
            self.fit_height_to_rows()

    def drive_row_height(self) -> int | None:
        return self._drive_row_height

    def set_text_sizes(self, folder_px: int, drive_px: int) -> None:
        """Row text sizes: folders take the view font, drives their own."""
        folder_px = max(1, int(folder_px))
        font = self.font()
        font.setPixelSize(folder_px)
        self.setFont(font)
        # A widget stylesheet outranks the application one, which pins the
        # folder tree's font-size.
        self.setStyleSheet(f"font-size: {folder_px}px;")
        self._drive_text_px = max(1, int(drive_px))
        self.scheduleDelayedItemsLayout()
        if self._drives_only:
            self.fit_height_to_rows()

    def drive_text_px(self) -> int | None:
        return self._drive_text_px

    def set_meter_metrics(self, height: int, gap: int) -> None:
        """Thickness of each drive's usage bar and its gap below the name."""
        height, gap = max(1, int(height)), max(0, int(gap))
        if (height, gap) == (self._meter_height, self._meter_gap):
            return
        self._meter_height, self._meter_gap = height, gap
        self.scheduleDelayedItemsLayout()
        if self._drives_only:
            self.fit_height_to_rows()

    def meter_height(self) -> int:
        return self._meter_height

    def meter_gap(self) -> int:
        return self._meter_gap

    def set_drive_icon_provider(self, provider) -> None:
        """``provider(path) -> QIcon | None`` replaces the shell's drive icons."""
        self._drive_icon_provider = provider
        self.viewport().update()

    def drive_icon_for(self, path: str) -> QIcon | None:
        provider = self._drive_icon_provider
        return provider(path) if provider is not None else None

    def fit_height_to_rows(self) -> None:
        """Drives-only lists size to their rows so the Folders section below
        gets the rest of the pane.

        Past :data:`MAX_VISIBLE_DRIVES` rows the list stops growing and scrolls
        instead, so a machine with many drives cannot crowd out the folders.
        """
        model = self.model()
        if model is None:
            return
        root = self.rootIndex()
        rows = model.rowCount(root)
        visible = min(rows, MAX_VISIBLE_DRIVES) if self._drives_only else rows
        height = 0
        for row in range(visible):
            height += max(0, self.sizeHintForRow(row))
        overflowing = self._drives_only and rows > MAX_VISIBLE_DRIVES
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if overflowing
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        frame = 2 * self.frameWidth()
        self.setFixedHeight(max(0, height) + frame + 2)

    def single_drive_expansion_enabled(self) -> bool:
        return self._single_drive_expansion_enabled

    def set_navigation_colors(self, selected_fill: QColor, hovered_fill: QColor) -> None:
        self._selected_row_fill = QColor(selected_fill)
        self._hovered_row_fill = QColor(hovered_fill)
        self.viewport().update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        position = event.position().toPoint()
        index = self.indexAt(position)
        item_rect = self.visualRect(index) if index.isValid() else QRect()
        clicked_disclosure = item_rect.isValid() and position.x() < item_rect.left()
        was_expanded = index.isValid() and self.isExpanded(index)
        model = self.model()
        expandable = bool(
            not self._drives_only and model is not None and index.isValid() and model.hasChildren(index)
        )
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
            fill = QColor(self._selected_row_fill if selected else self._hovered_row_fill)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(rect), 6, 6)
            painter.restore()
        # Depth below the displayed root, so a tree rooted at a drive starts
        # its folders flush left.
        # Rows directly under a drive root still get a branch column, so their
        # expand arrows line up with the rest of the tree.
        root = self.rootIndex()
        depth = 1 if root.isValid() else 0
        parent = index.parent()
        while parent.isValid() and parent != root:
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
    # The usage bar's thickness and gap live on the tree (meter_height/gap) so
    # the window can size them from layout_ratios.
    _USAGE_CACHE_SECONDS = 30.0

    def __init__(self, tree: FolderTreeView) -> None:
        super().__init__(tree)
        self._tree = tree
        self._usage_cache: dict[str, tuple[float, float | None]] = {}

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        hint = super().sizeHint(option, index)
        if self._is_drive(index):
            drive_px = self._tree.drive_text_px() or max(1, option.font.pixelSize())
            block = round(drive_px * 1.4) + self._tree.meter_gap() + self._tree.meter_height()
            height = max(self._tree.drive_row_height() or self.DRIVE_ROW_HEIGHT, block + 8)
        else:
            # Folder rows follow their text so larger type never clips.
            height = max(self.FOLDER_ROW_HEIGHT, round(self._tree.font().pixelSize() * 1.7))
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
        model = index.model()
        drive_path = model.filePath(index) if hasattr(model, "filePath") else ""
        custom_icon = self._tree.drive_icon_for(drive_path)
        drive_icon = custom_icon if custom_icon is not None and not custom_icon.isNull() else option.icon
        if not drive_icon.isNull():
            drive_icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)

        # Name and meter sit as one block centred in the row, sized from the
        # drive text so the meter never rides up into the name.
        drive_px = self._tree.drive_text_px() or max(1, option.font.pixelSize())
        text_height = round(drive_px * 1.4)
        block_height = text_height + self._tree.meter_gap() + self._tree.meter_height()
        block_top = rect.top() + max(0, (rect.height() - block_height) // 2)
        text_left = icon_rect.right() + 9
        text_rect = QRect(text_left, block_top, max(0, rect.right() - text_left - 6), text_height)
        color_role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        painter.setPen(option.palette.color(color_role))
        drive_font = QFont(option.font)
        drive_px = self._tree.drive_text_px()
        if drive_px:
            drive_font.setPixelSize(drive_px)
        drive_font.setWeight(QFont.Weight.DemiBold)   # drive name weight: 600
        painter.setFont(drive_font)
        label = QFontMetrics(drive_font).elidedText(
            option.text, Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)

        # The expanded drive is already identified by its open branch and gains
        # useful vertical room by omitting the capacity meter, as in the target.
        if not self._tree.isExpanded(index):
            ratio = self._drive_usage_ratio(index)
            if ratio is not None:
                track, (fill_start, fill_end) = self._tree.usage_bar_colors()
                bar = QRectF(
                    text_left,
                    block_top + text_height + self._tree.meter_gap(),
                    max(24, rect.right() - text_left - 7),
                    self._tree.meter_height(),
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(track)
                painter.drawRoundedRect(bar, 2, 2)
                if ratio > 0:
                    used = QRectF(bar)
                    used.setWidth(max(5.0, bar.width() * ratio))
                    gradient = QLinearGradient(bar.left(), 0.0, bar.right(), 0.0)
                    gradient.setColorAt(0.0, fill_start)
                    gradient.setColorAt(1.0, fill_end)
                    painter.setBrush(gradient)
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
