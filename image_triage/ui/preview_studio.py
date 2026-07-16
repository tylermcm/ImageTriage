"""Shared building blocks for the "Studio" popout preview design.

Behaviour-free Qt widgets, tokens, and helpers ported from the standalone
prototype (``scripts/preview_studio_prototype.py``) so the live
``FullScreenPreview`` and the prototype share one source of truth for the
Studio look. Widgets hold no application state — data arrives via setters and
selections leave via signals — mirroring how ``grid_card_renderer`` is a
behaviour-free painter shared by the grid and its prototype.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# --- Palette (the app's approved dark tokens) -------------------------------
GROUND = "#070707"
SURFACE_1 = "#0f1012"
SURFACE_2 = "#151516"
SURFACE_3 = "#1c1d20"
SURFACE_HOVER = "#24262b"
LINE = "#26282c"
LINE_STRONG = "#333842"
TEXT = "#f2f5f8"
TEXT_DIM = "#99a3b1"
TEXT_MUTE = "#6a727f"
ACCENT = "#3d7cff"
ACCENT_BRIGHT = "#78a0fa"
KEEPER = "#5fe684"
REJECT = "#ff6b6b"
GOLD = "#ffda5c"
INFO = "#70d2ff"

CARD_RADIUS = 10
PHOTO_RADIUS = 12


def studio_pen(color: str | QColor, width: float = 1.0) -> QPen:
    return QPen(QColor(color), width)


# --- Small composition helpers ----------------------------------------------
class Segmented(QFrame):
    """A pill of mutually-exclusive options. Emits ``selected`` with the chosen
    index; ``set_current`` reflects external state without emitting."""

    selected = Signal(int)

    def __init__(self, options: list[str], current: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        for i, label in enumerate(options):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(i == current)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, idx=i: self.selected.emit(idx))
            self._group.addButton(button, i)
            self.buttons.append(button)
            layout.addWidget(button)

    def set_current(self, index: int) -> None:
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)

    def current_index(self) -> int:
        return self._group.checkedId()


def card(title: str, summary: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    """A rounded inspector card with a title row; returns (frame, body layout).
    The summary label (right-aligned in the header) is stored on the frame as
    ``summary_label`` so callers can update it later."""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 13, 14, 13)
    layout.setSpacing(9)
    head = QHBoxLayout()
    head.setContentsMargins(0, 0, 0, 0)
    title_label = QLabel(title)
    title_label.setObjectName("cardTitle")
    head.addWidget(title_label)
    head.addStretch(1)
    frame.title_label = title_label  # type: ignore[attr-defined]
    frame.summary_label = None  # type: ignore[attr-defined]
    if summary is not None:
        summary_label = QLabel(summary)
        summary_label.setObjectName("cardSum")
        head.addWidget(summary_label)
        frame.summary_label = summary_label  # type: ignore[attr-defined]
    layout.addLayout(head)
    return frame, layout


def stat_row(key: str, value: str, *, warn: bool = False) -> tuple[QWidget, QLabel]:
    """A key/value row; returns (row, value label) so the value can be updated."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    key_label = QLabel(key)
    key_label.setObjectName("statKey")
    value_label = QLabel(value)
    value_label.setObjectName("statWarn" if warn else "statVal")
    layout.addWidget(key_label)
    layout.addStretch(1)
    layout.addWidget(value_label)
    return row, value_label


def control_row(label: str, control: QWidget) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    label_widget = QLabel(label)
    label_widget.setObjectName("ctrlLabel")
    layout.addWidget(label_widget)
    layout.addStretch(1)
    layout.addWidget(control)
    return row


# --- Inspector visuals ------------------------------------------------------
_DEFAULT_HISTOGRAM = [
    (0.0, 0.86), (0.16, 0.80), (0.30, 0.28), (0.42, 0.34),
    (0.55, 0.12), (0.66, 0.22), (0.80, 0.62), (1.0, 0.74),
]


class HistogramView(QWidget):
    """A luminance histogram: soft area fill, faint midlines, accent stroke,
    emphasized peak. ``set_curve`` accepts normalized points ``(x, y)`` where
    ``x`` runs 0..1 left-to-right and ``y`` runs 0 (top/tall) to 1
    (bottom/short); ``None`` restores the placeholder curve."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pts = list(_DEFAULT_HISTOGRAM)
        self.setFixedHeight(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_curve(self, points: list[tuple[float, float]] | None) -> None:
        self._pts = list(points) if points else list(_DEFAULT_HISTOGRAM)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()

        painter.setPen(studio_pen(QColor(28, 30, 34), 1.0))
        painter.drawLine(0, round(h * 0.5), w, round(h * 0.5))
        painter.setPen(studio_pen(QColor(24, 26, 30), 1.0))
        painter.drawLine(0, round(h * 0.25), w, round(h * 0.25))
        painter.drawLine(0, round(h * 0.75), w, round(h * 0.75))

        pts = self._pts
        curve = QPainterPath()
        curve.moveTo(0, h)
        for fx, fy in pts:
            curve.lineTo(fx * w, fy * h)
        curve.lineTo(w, h)
        curve.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(120, 160, 250, 52))
        painter.drawPath(curve)

        stroke = QPainterPath()
        for i, (fx, fy) in enumerate(pts):
            point = (fx * w, fy * h)
            stroke.moveTo(*point) if i == 0 else stroke.lineTo(*point)
        painter.setPen(studio_pen(ACCENT_BRIGHT, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(stroke)

        peak = min(pts, key=lambda p: p[1])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPoint(round(peak[0] * w), round(peak[1] * h)), 3, 3)
        painter.end()


class ConfidenceBar(QWidget):
    """A slim gold progress bar for an AI confidence percentage."""

    def __init__(self, pct: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pct = max(0, min(100, pct))
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_pct(self, pct: int) -> None:
        self._pct = max(0, min(100, int(pct)))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(SURFACE_3))
        painter.drawRoundedRect(QRectF(self.rect()), 3, 3)
        fill_w = max(6, round(self.width() * self._pct / 100))
        painter.setBrush(QColor(GOLD))
        painter.drawRoundedRect(QRectF(0, 0, fill_w, self.height()), 3, 3)
        painter.end()


# --- Filmstrip --------------------------------------------------------------
class ArrowButton(QPushButton):
    """A bare chevron scroll control (no enclosing circle), resting at 60%
    opacity and drawn with a soft halo so it stays legible over any
    thumbnail. The owner hides it when there is nothing more to scroll."""

    SIZE = 34

    def __init__(self, direction: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dir = direction  # "left" or "right"
        self._hover = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(1.0 if self._hover else 0.6)
        cx, cy = self.rect().center().x(), self.rect().center().y()
        reach = 7.5
        chevron = QPainterPath()
        if self._dir == "left":
            chevron.moveTo(cx + reach * 0.6, cy - reach)
            chevron.lineTo(cx - reach * 0.6, cy)
            chevron.lineTo(cx + reach * 0.6, cy + reach)
        else:
            chevron.moveTo(cx - reach * 0.6, cy - reach)
            chevron.lineTo(cx + reach * 0.6, cy)
            chevron.lineTo(cx - reach * 0.6, cy + reach)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        halo = QPen(QColor(0, 0, 0, 150), 4.2)
        halo.setCapStyle(Qt.PenCapStyle.RoundCap)
        halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(halo)
        painter.drawPath(chevron)
        glyph = QPen(QColor(255, 255, 255) if self._hover else QColor(230, 235, 242), 2.4)
        glyph.setCapStyle(Qt.PenCapStyle.RoundCap)
        glyph.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(glyph)
        painter.drawPath(chevron)
        painter.end()


_PLACEHOLDER_PALETTES = (
    ("#2b3a3f", "#141d22"),
    ("#37474f", "#161f24"),
    ("#3a4f58", "#141d22"),
    ("#2f4048", "#161f24"),
    ("#334349", "#141d22"),
    ("#3a4f58", "#161f24"),
)


class FilmstripThumb(QWidget):
    """One rounded thumbnail. Draws ``pixmap`` when supplied, otherwise a
    placeholder landscape. Carries a status tag dot and its 1-based frame
    number, rings the current frame, and emits ``clicked`` with its 0-based
    frame index."""

    clicked = Signal(int)

    BASE_HEIGHT = 76
    BASE_MIN_WIDTH = 84

    def __init__(
        self,
        index: int,
        tag: str | None,
        current: bool,
        pixmap: QPixmap | None = None,
        parent: QWidget | None = None,
        height: int = BASE_HEIGHT,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._tag = tag
        self._current = current
        self._pixmap = pixmap
        self.set_thumb_height(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_thumb_height(self, height: int) -> None:
        height = max(24, int(height))
        self.setFixedHeight(height)
        self.setMinimumWidth(round(self.BASE_MIN_WIDTH * height / self.BASE_HEIGHT))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._index)
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 8, 8)
        painter.setClipPath(path)
        if self._pixmap is not None and not self._pixmap.isNull():
            scaled = self._pixmap.size()
            # Landscape fills the tile (cover crop); portrait fits inside it
            # (pillarboxed) — covering would blow a portrait up to the tile's
            # width, center-cropping a sliver and wrecking the resolution.
            portrait = self._pixmap.height() > self._pixmap.width()
            mode = (
                Qt.AspectRatioMode.KeepAspectRatio
                if portrait
                else Qt.AspectRatioMode.KeepAspectRatioByExpanding
            )
            scaled.scale(rect.size(), mode)
            draw = QRect(QPoint(0, 0), scaled)
            draw.moveCenter(rect.center())
            painter.fillRect(rect, QColor(17, 18, 20))
            painter.drawPixmap(draw, self._pixmap)
        else:
            sky, ground = _PLACEHOLDER_PALETTES[self._index % len(_PLACEHOLDER_PALETTES)]
            painter.fillRect(rect, QColor(sky))
            mountain = QPainterPath()
            mountain.moveTo(rect.left(), rect.bottom())
            mountain.lineTo(rect.left() + rect.width() * 0.30, rect.top() + rect.height() * 0.55)
            mountain.lineTo(rect.left() + rect.width() * 0.50, rect.top() + rect.height() * 0.70)
            mountain.lineTo(rect.right(), rect.top() + rect.height() * 0.42)
            mountain.lineTo(rect.right(), rect.bottom())
            mountain.closeSubpath()
            painter.fillPath(mountain, QColor(ground))
        painter.setClipping(False)

        if self._tag:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._tag))
            painter.drawEllipse(QPoint(rect.left() + 9, rect.top() + 9), 4, 4)

        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#dfe6ee"))
        painter.drawText(
            rect.adjusted(0, 0, -6, -3),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
            str(self._index + 1),
        )

        if self._current:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(studio_pen(QColor(0, 0, 0, 150), 1.0))
            painter.drawRoundedRect(QRectF(rect).adjusted(1.5, 1.5, -1.5, -1.5), 6, 6)
            painter.setPen(studio_pen(ACCENT_BRIGHT, 2.0))
            painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        painter.end()


# Placeholder source used until the owner calls ``set_source`` (Phase 1 look).
_PLACEHOLDER_TOTAL = 248
_PLACEHOLDER_CURRENT = 11  # 0-based -> frame 12
_PLACEHOLDER_TAGS = {9: REJECT, 10: INFO, 11: KEEPER}


class FilmstripHandle(QWidget):
    """The grab tab on the filmstrip's top edge: three grip lines signalling a
    draggable divider. Drag up/down to resize the strip; drag it closed (or
    click) to hide, leaving the tab peeking so it can be pulled back up."""

    HEIGHT = 14
    GRIP_W = 36

    def __init__(self, strip: "Filmstrip") -> None:
        super().__init__(strip)
        self._strip = strip
        self._hover = False
        self._press_global_y: int | None = None
        self._press_thumb_h = 0
        self._moved = False
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global_y = int(event.globalPosition().y())
            self._press_thumb_h = 0 if self._strip.is_collapsed() else self._strip.thumb_height()
            self._moved = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._press_global_y is not None and event.buttons() & Qt.MouseButton.LeftButton:
            dy = int(event.globalPosition().y()) - self._press_global_y
            if abs(dy) > 3:
                self._moved = True
            if self._moved:
                # Handle sits on top: dragging up grows the strip.
                self._strip.drag_resize(self._press_thumb_h - dy)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            if self._press_global_y is not None and not self._moved:
                self._strip.toggle_collapsed()
            self._press_global_y = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx = self.width() / 2
        if self._hover:
            pill = QRectF(cx - self.GRIP_W / 2 - 8, 1.5, self.GRIP_W + 16, self.HEIGHT - 3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(SURFACE_HOVER))
            painter.drawRoundedRect(pill, 5, 5)
        pen = QPen(QColor(TEXT if self._hover else TEXT_MUTE), 1.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        x0 = cx - self.GRIP_W / 2
        x1 = cx + self.GRIP_W / 2
        for y in (4.0, 7.0, 10.0):
            painter.drawLine(QPoint(round(x0), round(y)), QPoint(round(x1), round(y)))
        painter.end()


class Filmstrip(QFrame):
    """A full-width reel of thumbnails that fills the strip edge to edge, with
    bare chevron scroll arrows floating over each end. It packs in as many
    frames as fit and repopulates on resize; the arrows page through the full
    set and hide at the extremes. Middle-focused strips always use an odd slot
    count and pad the ends so the current frame stays in the physical center.

    ``focus`` places the current frame at a fixed slot ("second" or "middle").
    Without a source it renders placeholder thumbs; ``set_source`` supplies the
    real total/current plus optional thumbnail and tag providers. Clicking a
    thumb (or, implicitly, the arrows) emits ``frame_selected`` with the target
    0-based index.
    """

    frame_selected = Signal(int)
    # User changed the strip's thumb height or collapsed it (drag handle);
    # owners persist the new layout from here.
    layout_changed = Signal()

    THUMB_W = 116       # target width at the default height; scales with height
    GAP = 6
    MARGIN = 9
    ARROW_INSET_FRAC = 0.008
    MIN_THUMB_H = 44
    MAX_THUMB_H = 168
    DEFAULT_THUMB_H = 76
    REEL_TOP_PAD = 2
    REEL_BOTTOM_PAD = 8

    def __init__(self, focus: str = "middle", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("filmstrip")
        self._focus = "second" if focus == "second" else "middle"

        self._total = _PLACEHOLDER_TOTAL
        self._current = _PLACEHOLDER_CURRENT
        self._thumb_provider: Callable[[int], QPixmap | None] | None = None
        self._tag_provider: Callable[[int], str | None] | None = _PLACEHOLDER_TAGS.get

        self._thumb_h = self.DEFAULT_THUMB_H
        self._collapsed = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._handle = FilmstripHandle(self)
        outer.addWidget(self._handle)
        self._reel = QWidget()
        self._layout = QHBoxLayout(self._reel)
        self._layout.setContentsMargins(self.MARGIN, self.REEL_TOP_PAD, self.MARGIN, self.REEL_BOTTOM_PAD)
        self._layout.setSpacing(self.GAP)
        outer.addWidget(self._reel, 1)

        self._left = ArrowButton("left", self)
        self._left.clicked.connect(lambda: self._scroll(-1))
        self._right = ArrowButton("right", self)
        self._right.clicked.connect(lambda: self._scroll(1))

        self._offset = 0        # index of first visible frame (0-based)
        self._count = 0
        self._recenter_pending = True
        self._apply_strip_height()

    # -- resize / collapse (drag handle) ----------------------------------
    def thumb_height(self) -> int:
        return self._thumb_h

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _apply_strip_height(self) -> None:
        if self._collapsed:
            self._reel.hide()
            self._left.hide()
            self._right.hide()
            self.setFixedHeight(FilmstripHandle.HEIGHT)
        else:
            self._reel.show()
            self.setFixedHeight(
                FilmstripHandle.HEIGHT + self.REEL_TOP_PAD + self._thumb_h + self.REEL_BOTTOM_PAD
            )

    def drag_resize(self, target_thumb_h: int) -> None:
        """Live resize from the handle drag; dragging well below the minimum
        collapses the strip to just the tab."""
        if target_thumb_h < self.MIN_THUMB_H - 16:
            if not self._collapsed:
                self._collapsed = True
                self._apply_strip_height()
                self.layout_changed.emit()
            return
        previous = (self._collapsed, self._thumb_h)
        self._collapsed = False
        self._thumb_h = max(self.MIN_THUMB_H, min(self.MAX_THUMB_H, int(target_thumb_h)))
        self._apply_strip_height()
        # Recompute how many thumbs fit at the new size and repopulate — taller
        # thumbs are wider, so fewer fit (arrows appear); shorter fit more.
        self._recenter_pending = False
        self._reflow(force=True)
        if previous != (self._collapsed, self._thumb_h):
            self.layout_changed.emit()

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self._apply_strip_height()
        if not self._collapsed:
            self._reflow(force=True)
        self.layout_changed.emit()

    def restore_layout(self, thumb_h: int, collapsed: bool) -> None:
        """Apply a persisted strip layout without emitting ``layout_changed``."""
        self._thumb_h = max(self.MIN_THUMB_H, min(self.MAX_THUMB_H, int(thumb_h)))
        self._collapsed = bool(collapsed)
        self._apply_strip_height()
        if not self._collapsed:
            self._reflow(force=True)

    def set_source(
        self,
        total: int,
        current: int,
        thumb_provider: Callable[[int], QPixmap | None] | None = None,
        tag_provider: Callable[[int], str | None] | None = None,
    ) -> None:
        self._total = max(0, int(total))
        self._current = max(0, min(self._total - 1, int(current))) if self._total else 0
        self._thumb_provider = thumb_provider
        self._tag_provider = tag_provider
        self._recenter_pending = True
        self._reflow(force=True)

    def set_current(self, current: int) -> None:
        if not self._total:
            return
        self._current = max(0, min(self._total - 1, int(current)))
        self._recenter_pending = True
        self._reflow(force=True)

    def refresh(self) -> None:
        """Repopulate visible thumbs (e.g. after async thumbnails arrive)
        without moving the scroll position."""
        self._populate()
        self._position_arrows()

    def _target_thumb_w(self) -> int:
        # Keep the landscape aspect as the strip grows or shrinks.
        return max(48, round(self.THUMB_W * self._thumb_h / self.DEFAULT_THUMB_H))

    def _fit_count(self) -> int:
        avail = self.width() - self.MARGIN * 2
        fit = max(1, (avail + self.GAP) // (self._target_thumb_w() + self.GAP))
        if self._focus == "middle" and fit % 2 == 0:
            fit = max(1, fit - 1)
        return fit

    def _focus_index(self) -> int:
        return 1 if self._focus == "second" else self._count // 2

    def _tag_for(self, index: int) -> str | None:
        return self._tag_provider(index) if self._tag_provider is not None else None

    def _thumb_for(self, index: int) -> QPixmap | None:
        return self._thumb_provider(index) if self._thumb_provider is not None else None

    def _populate(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Hide immediately (deleteLater is async, so the old thumbs
                # would otherwise linger and ghost during a rapid drag). NOT
                # setParent(None) — that reparents a visible widget into its
                # own floating top-level window.
                widget.hide()
                widget.deleteLater()
        if self._collapsed:
            self._left.hide()
            self._right.hide()
            return
        # Only stretch thumbs to fill the row when the reel is scrollable (more
        # frames than fit). When they all fit, keep them at their natural
        # landscape width and center the complete slot group.
        fill = self._total > self._count
        target_w = self._target_thumb_w()
        if not fill:
            self._layout.addStretch(1)
        for slot in range(self._count):
            index = self._offset + slot
            if not 0 <= index < self._total:
                spacer = QWidget()
                spacer.setFixedHeight(self._thumb_h)
                spacer.setMinimumWidth(
                    round(FilmstripThumb.BASE_MIN_WIDTH * self._thumb_h / FilmstripThumb.BASE_HEIGHT)
                )
                spacer.setSizePolicy(
                    QSizePolicy.Policy.Expanding if fill else QSizePolicy.Policy.Fixed,
                    QSizePolicy.Policy.Fixed,
                )
                if not fill:
                    spacer.setFixedWidth(target_w)
                self._layout.addWidget(spacer)
                continue
            thumb = FilmstripThumb(
                index,
                self._tag_for(index),
                index == self._current,
                self._thumb_for(index),
                height=self._thumb_h,
            )
            if not fill:
                thumb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                thumb.setFixedWidth(target_w)
            thumb.clicked.connect(self.frame_selected)
            self._layout.addWidget(thumb)
        if not fill:
            self._layout.addStretch(1)
        self._left.setVisible(self._total > 0 and self._current > 0)
        self._right.setVisible(self._total > 0 and self._current < self._total - 1)
        self._left.raise_()
        self._right.raise_()

    def _scroll(self, direction: int) -> None:
        step = max(1, self._count // 2)
        target = max(0, min(self._total - 1, self._current + direction * step)) if self._total else 0
        if target == self._current:
            return
        self._current = target
        self._recenter_pending = True
        self._reflow(force=True)
        self.frame_selected.emit(target)

    def _position_arrows(self) -> None:
        # Centered over the reel area (below the drag handle).
        reel_h = max(0, self.height() - FilmstripHandle.HEIGHT)
        y = FilmstripHandle.HEIGHT + (reel_h - ArrowButton.SIZE) // 2
        inset = round(self.width() * self.ARROW_INSET_FRAC)
        self._left.move(inset, y)
        self._right.move(self.width() - inset - ArrowButton.SIZE, y)
        self._left.raise_()
        self._right.raise_()

    def _reflow(self, *, force: bool) -> None:
        fit = self._fit_count()
        changed = fit != self._count
        self._count = fit
        if self._recenter_pending or force or changed:
            if self._focus == "middle":
                # Negative/start-overflow offsets are intentional: _populate
                # turns them into empty slots so edge frames remain centered.
                self._offset = self._current - self._focus_index()
            else:
                max_offset = max(0, self._total - self._count)
                self._offset = min(max_offset, max(0, self._current - self._focus_index()))
            self._recenter_pending = False
        else:
            max_offset = max(0, self._total - self._count)
            if self._focus != "middle":
                self._offset = min(self._offset, max_offset)
        if changed or force:
            self._populate()
        self._position_arrows()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._reflow(force=False)
        super().resizeEvent(event)


# --- Stylesheet -------------------------------------------------------------
def scope_stylesheet(css: str, scope: str) -> str:
    """Prefix every rule's selectors with ``scope`` (e.g. an ancestor id) to
    raise specificity. Needed when this sheet lives inside a dialog that
    inherits a broader application stylesheet whose plain type selectors would
    otherwise tie with, and override, the Studio rules."""
    if not scope:
        return css
    parts = []
    for block in css.split("}"):
        if "{" not in block:
            parts.append(block)
            continue
        selectors, props = block.split("{", 1)
        scoped = ", ".join(
            f"{scope} {selector.strip()}" for selector in selectors.split(",") if selector.strip()
        )
        parts.append(f"{scoped} {{{props}")
    return "}".join(parts)


def studio_stylesheet(scope: str = "") -> str:
    """The Studio stylesheet, keyed by object names the widgets above set.
    Pass ``scope`` to prefix every selector (see ``scope_stylesheet``)."""
    css = f"""
    QWidget#studioRoot {{ background: {GROUND}; }}

    QFrame#toolbar {{ background: {SURFACE_1}; border: 1px solid {LINE}; border-radius: {CARD_RADIUS}px; }}
    QFrame#filmstrip {{ background: {SURFACE_1}; border: 1px solid {LINE}; border-radius: {CARD_RADIUS}px; }}
    QFrame#rail {{ background: {SURFACE_1}; border: 1px solid {LINE}; border-radius: {CARD_RADIUS}px; }}
    QFrame#vline {{ background: {LINE}; border: none; }}

    QLabel#groupLabel {{ color: {TEXT_MUTE}; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}

    QFrame#segmented {{ background: {SURFACE_3}; border: 1px solid {LINE}; border-radius: 9px; }}
    QFrame#segmented QPushButton {{
        background: transparent; border: none; color: {TEXT_DIM};
        padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500;
        min-height: 0px;
    }}
    QFrame#segmented QPushButton:hover {{ background: {SURFACE_HOVER}; color: {TEXT}; }}
    QFrame#segmented QPushButton:checked {{ background: {ACCENT}; color: #ffffff; }}
    QFrame#segmented QPushButton:disabled {{ color: {TEXT_MUTE}; }}
    QFrame#segmented QPushButton:checked:disabled {{ background: {SURFACE_HOVER}; color: {TEXT_MUTE}; }}

    QPushButton#toolBtn {{
        background: {SURFACE_3}; border: 1px solid {LINE}; color: {TEXT};
        padding: 7px 13px; border-radius: 8px; font-size: 12px; font-weight: 500;
        min-height: 0px;
    }}
    QPushButton#toolBtn:hover {{ background: {SURFACE_HOVER}; border-color: {LINE_STRONG}; }}
    QPushButton#toolBtn:checked {{ background: {ACCENT}; color: #ffffff; border-color: {ACCENT}; }}
    QPushButton#toolBtn:disabled {{ color: {TEXT_MUTE}; border-color: {LINE}; }}
    QPushButton#ghostBtn {{ background: transparent; border: 1px solid transparent; color: {TEXT_DIM}; padding: 7px 11px; border-radius: 8px; }}
    QPushButton#ghostBtn:hover {{ background: {SURFACE_HOVER}; color: {TEXT}; }}

    QFrame#navPill {{ background: {SURFACE_3}; border: 1px solid {LINE}; border-radius: 9px; }}
    QPushButton#navArrow {{ background: transparent; border: none; color: {TEXT_DIM}; padding: 4px 10px; border-radius: 6px; font-size: 15px; }}
    QPushButton#navArrow:hover {{ background: {SURFACE_HOVER}; color: {TEXT}; }}
    QLabel#navCount {{ color: {TEXT}; padding: 0 8px; min-width: 58px; }}

    QFrame#card {{ background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: {CARD_RADIUS}px; }}
    QLabel#cardTitle {{ font-size: 13px; font-weight: 600; color: {TEXT}; }}
    QLabel#cardSum {{ font-size: 11px; color: {TEXT_MUTE}; }}
    QLabel#statKey {{ color: {TEXT_MUTE}; font-size: 12px; }}
    QLabel#statVal {{ color: {TEXT}; font-size: 12px; }}
    QLabel#statWarn {{ color: {GOLD}; font-size: 12px; }}
    QLabel#ctrlLabel {{ color: {TEXT_DIM}; font-size: 12px; }}
    QLabel#confPct {{ color: {GOLD}; font-size: 13px; font-weight: 600; }}
    QLabel#reason {{ color: {TEXT_DIM}; font-size: 12px; }}

    QComboBox {{
        background: {SURFACE_3}; border: 1px solid {LINE}; color: {TEXT};
        padding: 5px 9px; border-radius: 6px; font-size: 11px; min-width: 96px;
        min-height: 0px;
    }}
    QComboBox#colorCombo {{ min-width: 0px; }}
    QComboBox:hover {{ border-color: {LINE_STRONG}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {LINE_STRONG}; color: {TEXT}; selection-background-color: {ACCENT}; }}
    """
    return scope_stylesheet(css, scope)


__all__ = [
    "GROUND", "SURFACE_1", "SURFACE_2", "SURFACE_3", "SURFACE_HOVER",
    "LINE", "LINE_STRONG", "TEXT", "TEXT_DIM", "TEXT_MUTE",
    "ACCENT", "ACCENT_BRIGHT", "KEEPER", "REJECT", "GOLD", "INFO",
    "CARD_RADIUS", "PHOTO_RADIUS",
    "studio_pen", "studio_stylesheet", "scope_stylesheet",
    "Segmented", "card", "stat_row", "control_row",
    "HistogramView", "ConfidenceBar",
    "ArrowButton", "FilmstripThumb", "FilmstripHandle", "Filmstrip",
]
