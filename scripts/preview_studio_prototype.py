"""Standalone prototype for the redesigned "Studio" popout preview.

Design mockup only — intentionally NOT wired into the live FullScreenPreview
(image_triage/preview.py). It explores the approved "Studio" direction so the
layout, spacing, and card styling can be tuned in real Qt before any of it is
migrated into the app:

    * grouped toolbar (Review / Edit) + navigation pill
    * rounded image stage with corner badges (keeper chip, AI pick, focus chip)
    * persistent inspector rail: histogram, focus peaking, FITS, AI rationale
    * rounded filmstrip with the current frame ringed in the accent blue
    * status action bar (reject / keeper + counts)

Run:  python scripts/preview_studio_prototype.py [--image PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImageReader,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from grid_card_prototype import make_dummy_landscape

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


def _pen(color: str | QColor, width: float = 1.0) -> QPen:
    pen = QPen(QColor(color), width)
    return pen


class ImageStage(QWidget):
    """The focused image pane: a rounded photo on the viewport ground with the
    grid card's corner-badge language (keeper chip, AI pick, focus chip)."""

    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__()
        self._pixmap = pixmap
        self.setMinimumSize(420, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor(GROUND))

        pad = 12
        photo = self.rect().adjusted(pad, pad, -pad, -pad)
        path = QPainterPath()
        path.addRoundedRect(QRectF(photo), PHOTO_RADIUS, PHOTO_RADIUS)

        painter.save()
        painter.setClipPath(path)
        painter.fillRect(photo, QColor(10, 13, 16))
        if self._pixmap is not None and not self._pixmap.isNull():
            scaled = self._pixmap.size()
            scaled.scale(photo.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            draw = QRect(QPoint(0, 0), scaled)
            draw.moveCenter(photo.center())
            painter.drawPixmap(draw, self._pixmap)
        painter.restore()

        # Focused selection ring (accent), matching the grid card treatment.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(QColor(0, 0, 0, 150), 1.0))
        painter.drawRoundedRect(QRectF(photo).adjusted(2, 2, -2, -2), PHOTO_RADIUS - 1, PHOTO_RADIUS - 1)
        painter.setPen(_pen(ACCENT_BRIGHT, 1.8))
        painter.drawRoundedRect(QRectF(photo).adjusted(0.5, 0.5, -0.5, -0.5), PHOTO_RADIUS, PHOTO_RADIUS)
        painter.end()


class HistogramView(QWidget):
    """A luminance histogram: soft area fill, faint midlines, accent stroke,
    emphasized peak — matching the mockup's data-viz treatment."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self.width()
        h = self.height()

        painter.setPen(_pen(QColor(28, 30, 34), 1.0))
        painter.drawLine(0, round(h * 0.5), w, round(h * 0.5))
        painter.setPen(_pen(QColor(24, 26, 30), 1.0))
        painter.drawLine(0, round(h * 0.25), w, round(h * 0.25))
        painter.drawLine(0, round(h * 0.75), w, round(h * 0.75))

        # A smooth curve peaking left-of-center (typical exposure).
        pts = [
            (0.0, 0.86), (0.16, 0.80), (0.30, 0.28), (0.42, 0.34),
            (0.55, 0.12), (0.66, 0.22), (0.80, 0.62), (1.0, 0.74),
        ]
        curve = QPainterPath()
        curve.moveTo(0, h)
        for i, (fx, fy) in enumerate(pts):
            x, y = fx * w, fy * h
            curve.lineTo(x, y) if i == 0 else curve.lineTo(x, y)
        curve.lineTo(w, h)
        curve.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(120, 160, 250, 52))
        painter.drawPath(curve)

        stroke = QPainterPath()
        for i, (fx, fy) in enumerate(pts):
            x, y = fx * w, fy * h
            stroke.moveTo(x, y) if i == 0 else stroke.lineTo(x, y)
        painter.setPen(_pen(ACCENT_BRIGHT, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(stroke)

        peak = min(pts, key=lambda p: p[1])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPoint(round(peak[0] * w), round(peak[1] * h)), 3, 3)
        painter.end()


class FilmstripThumb(QWidget):
    """A rounded landscape thumbnail with a status tag + frame number; the
    current frame gets the accent ring."""

    _PALETTES = (
        ("#2b3a3f", "#141d22"),
        ("#37474f", "#161f24"),
        ("#3a4f58", "#141d22"),
        ("#2f4048", "#161f24"),
        ("#334349", "#141d22"),
        ("#3a4f58", "#161f24"),
    )

    def __init__(self, number: int, tag: str | None, current: bool) -> None:
        super().__init__()
        self._number = number
        self._tag = tag
        self._current = current
        # Flexible width so a row of thumbs fills the strip edge to edge;
        # height is fixed to keep the landscape proportions readable.
        self.setFixedHeight(76)
        self.setMinimumWidth(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 8, 8)
        painter.setClipPath(path)
        sky, ground = self._PALETTES[self._number % len(self._PALETTES)]
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
        painter.drawText(rect.adjusted(0, 0, -6, -3), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, str(self._number))

        if self._current:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_pen(QColor(0, 0, 0, 150), 1.0))
            painter.drawRoundedRect(QRectF(rect).adjusted(1.5, 1.5, -1.5, -1.5), 6, 6)
            painter.setPen(_pen(ACCENT_BRIGHT, 2.0))
            painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        painter.end()


# Which frames carry a status tag dot in the strip (frame number -> color).
FILM_TAGS = {10: REJECT, 11: INFO, 12: KEEPER}
FILM_CURRENT = 12
FILM_TOTAL = 248


class ArrowButton(QPushButton):
    """A circular scroll control drawn as an encircled chevron. Used at each
    end of the filmstrip; the owner hides it when there is nothing more to
    scroll in that direction."""

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
        # Resting at 25% opacity (75% transparent); full strength on hover.
        painter.setOpacity(1.0 if self._hover else 0.25)
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

        # No enclosing circle — just the chevron, with a soft dark halo so it
        # stays legible over both bright and dark thumbnails.
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


class Filmstrip(QFrame):
    """A full-width reel of thumbnails that fills the strip edge to edge, with
    encircled scroll arrows floating semi-transparent over each end. The reel
    packs in as many frames as fit and repopulates on resize; the arrows page
    through the full set and hide at the extremes. ``focus`` places the current
    frame at a fixed slot: "second" (2nd from the left) or "middle"."""

    THUMB_W = 116       # target width; thumbs flex around this to fill the row
    GAP = 6             # was 10 — tightened ~40% so the panes sit closer
    MARGIN = 9
    ARROW_INSET_FRAC = 0.008  # overlay arrows sit this fraction of the width in from each edge

    def __init__(self, focus: str = "middle") -> None:
        super().__init__()
        self.setObjectName("filmstrip")
        self._focus = "second" if focus == "second" else "middle"

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(self.MARGIN, 8, self.MARGIN, 8)
        self._layout.setSpacing(self.GAP)

        # Arrows are children (not laid out) so they can overlay the ends while
        # the thumbnails run the full width underneath.
        self._left = ArrowButton("left", self)
        self._left.clicked.connect(lambda: self._scroll(-1))
        self._right = ArrowButton("right", self)
        self._right.clicked.connect(lambda: self._scroll(1))

        self._offset = 0        # index of first visible frame (0-based)
        self._count = 0
        self._initialized = False

    def _fit_count(self) -> int:
        avail = self.width() - self.MARGIN * 2
        return max(1, (avail + self.GAP) // (self.THUMB_W + self.GAP))

    def _focus_index(self) -> int:
        return 1 if self._focus == "second" else self._count // 2

    def _populate(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for i in range(self._count):
            number = self._offset + i + 1  # frames are 1-based
            self._layout.addWidget(
                FilmstripThumb(number, FILM_TAGS.get(number), number == FILM_CURRENT)
            )
        self._left.setVisible(self._offset > 0)
        self._right.setVisible(self._offset + self._count < FILM_TOTAL)
        self._left.raise_()
        self._right.raise_()

    def _scroll(self, direction: int) -> None:
        step = max(1, self._count // 2)
        max_offset = max(0, FILM_TOTAL - self._count)
        self._offset = min(max_offset, max(0, self._offset + direction * step))
        self._populate()

    def _position_arrows(self) -> None:
        y = (self.height() - ArrowButton.SIZE) // 2
        inset = round(self.width() * self.ARROW_INSET_FRAC)
        self._left.move(inset, y)
        self._right.move(self.width() - inset - ArrowButton.SIZE, y)
        self._left.raise_()
        self._right.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        fit = self._fit_count()
        if fit != self._count:
            self._count = fit
            max_offset = max(0, FILM_TOTAL - self._count)
            if not self._initialized:
                self._offset = min(max_offset, max(0, FILM_CURRENT - 1 - self._focus_index()))
                self._initialized = True
            else:
                self._offset = min(self._offset, max_offset)
            self._populate()
        self._position_arrows()
        super().resizeEvent(event)


def _segmented(options: list[str], current: int) -> QFrame:
    frame = QFrame()
    frame.setObjectName("segmented")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(3, 3, 3, 3)
    layout.setSpacing(2)
    group = QButtonGroup(frame)
    group.setExclusive(True)
    for i, label in enumerate(options):
        button = QPushButton(label)
        button.setCheckable(True)
        button.setChecked(i == current)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        group.addButton(button)
        layout.addWidget(button)
    frame._group = group  # keep a ref alive
    return frame


def _card(title: str, summary: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(14, 13, 14, 13)
    layout.setSpacing(9)
    head = QHBoxLayout()
    head.setContentsMargins(0, 0, 0, 0)
    title_label = QLabel(title)
    title_label.setObjectName("cardTitle")
    head.addWidget(title_label)
    head.addStretch(1)
    if summary is not None:
        sum_label = QLabel(summary)
        sum_label.setObjectName("cardSum")
        head.addWidget(sum_label)
    layout.addLayout(head)
    return card, layout


def _stat_row(key: str, value: str, *, warn: bool = False) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    k = QLabel(key)
    k.setObjectName("statKey")
    v = QLabel(value)
    v.setObjectName("statWarn" if warn else "statVal")
    layout.addWidget(k)
    layout.addStretch(1)
    layout.addWidget(v)
    return row


def _control_row(label: str, control: QWidget) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    lbl = QLabel(label)
    lbl.setObjectName("ctrlLabel")
    layout.addWidget(lbl)
    layout.addStretch(1)
    layout.addWidget(control)
    return row


class ConfidenceBar(QWidget):
    def __init__(self, pct: int) -> None:
        super().__init__()
        self._pct = pct
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

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


class StudioPreviewPrototype(QMainWindow):
    def __init__(self, pixmap: QPixmap, load_message: str, film_focus: str = "middle") -> None:
        super().__init__()
        self.setWindowTitle("Popout Preview — Studio Prototype")
        self.resize(1200, 800)

        self.stage = ImageStage(pixmap)
        self.rail = self._build_rail()
        self.filmstrip = Filmstrip(focus=film_focus)

        root = QWidget()
        root.setObjectName("root")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_toolbar())

        # Body: image stage + collapsible inspector rail. The filmstrip is a
        # separate full-width row below, so it spans the window regardless of
        # the rail's state — collapsing the rail just lets the stage widen to
        # fill the freed space while the filmstrip stays put.
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.stage, 1)
        body_layout.addWidget(self.rail)
        root_layout.addWidget(body, 1)
        root_layout.addWidget(self.filmstrip)

        # Bottom status bar removed for this iteration (counts/position). The
        # _build_statusbar helper is kept so it can be reinstated in one line.
        _ = load_message
        self.setCentralWidget(root)
        self.setStyleSheet(self._stylesheet())

    # -- toolbar -------------------------------------------------------------
    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("toolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(12)

        layout.addWidget(self._group_label("Review"))
        layout.addWidget(_segmented(["1-Up", "Compare", "Auto-Bracket"], 0))
        layout.addWidget(self._divider())
        layout.addWidget(self._group_label("Edit"))
        layout.addWidget(self._tool_button("Edit 1/1"))
        layout.addWidget(self._tool_button("Photoshop"))
        layout.addStretch(1)
        self.inspector_toggle = self._tool_button("Inspector")
        self.inspector_toggle.setCheckable(True)
        self.inspector_toggle.setChecked(True)
        self.inspector_toggle.setToolTip("Show or hide the inspector rail")
        self.inspector_toggle.toggled.connect(self._toggle_inspector)
        layout.addWidget(self.inspector_toggle)
        layout.addWidget(self._nav_pill())
        close = self._tool_button("✕")
        close.setObjectName("ghostBtn")
        layout.addWidget(close)
        return bar

    def _toggle_inspector(self, shown: bool) -> None:
        # Hiding the rail drops it from the body layout, so the image stage
        # (stretch 1) widens to fill; the full-width filmstrip is unaffected.
        self.rail.setVisible(shown)

    def _group_label(self, text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setObjectName("groupLabel")
        return label

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setObjectName("vline")
        line.setFixedWidth(1)
        return line

    def _tool_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("toolBtn")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _nav_pill(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("navPill")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        prev = QPushButton("‹")
        prev.setObjectName("navArrow")
        count = QLabel("12 / 248")
        count.setObjectName("navCount")
        count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nxt = QPushButton("›")
        nxt.setObjectName("navArrow")
        for widget in (prev, count, nxt):
            if isinstance(widget, QPushButton):
                widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                widget.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(widget)
        return frame

    # -- inspector rail ------------------------------------------------------
    def _build_rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(312)
        layout = QVBoxLayout(rail)
        # Left inset is 0 so the gap from the image to the first card equals
        # the image stage's own 12px pad — matching the 12px gap between cards.
        layout.setContentsMargins(0, 12, 12, 12)
        layout.setSpacing(12)

        hist_card, hist_layout = _card("Histogram", "Luma")
        hist_layout.addWidget(HistogramView())
        hist_layout.addWidget(_stat_row("Exposure", "+0.3 EV"))
        hist_layout.addWidget(_stat_row("Clipping", "0.4% hi · 0% lo", warn=True))
        layout.addWidget(hist_card)

        focus_card, focus_layout = _card("Focus Peaking", "On")
        focus_layout.addWidget(_control_row("Enabled", _segmented(["Off", "On"], 1)))
        color_combo = QComboBox()
        color_combo.setObjectName("colorCombo")
        color_combo.addItems(["Red", "Green", "Blue", "Yellow"])
        color_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        color_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        focus_layout.addWidget(_control_row("Color", color_combo))
        focus_layout.addWidget(_control_row("Sensitivity", _segmented(["Low", "Med", "High"], 1)))
        focus_layout.addWidget(_control_row("Background", _segmented(["Dimmed", "Original"], 0)))
        layout.addWidget(focus_card)

        fits_card, fits_layout = _card("FITS Display", "Auto STF")
        stretch_combo = QComboBox()
        stretch_combo.addItems(["Auto STF", "Linear", "Asinh", "Log"])
        stretch_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        fits_layout.addWidget(_control_row("Stretch", stretch_combo))
        layout.addWidget(fits_card)

        ai_card, ai_layout = _card("Why AI picked this")
        conf_row = QWidget()
        conf_layout = QHBoxLayout(conf_row)
        conf_layout.setContentsMargins(0, 0, 0, 0)
        conf_layout.setSpacing(10)
        conf_layout.addWidget(ConfidenceBar(99), 1)
        pct = QLabel("99")
        pct.setObjectName("confPct")
        conf_layout.addWidget(pct)
        ai_layout.addWidget(conf_row)
        for reason in (
            "Sharpest frame in the burst",
            "Eyes in focus, low motion blur",
            "Best exposure balance of 3",
        ):
            item = QLabel(f"›  {reason}")
            item.setObjectName("reason")
            item.setWordWrap(True)
            ai_layout.addWidget(item)
        layout.addWidget(ai_card)

        layout.addStretch(1)
        return rail

    # -- filmstrip -----------------------------------------------------------
    # -- status bar ----------------------------------------------------------
    def _build_statusbar(self, load_message: str) -> QWidget:
        bar = QFrame()
        bar.setObjectName("statusbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)

        position = QLabel("Frame 12 of 248")
        position.setObjectName("counts")
        layout.addWidget(position)
        layout.addStretch(1)
        counts = QLabel("41 keepers  ·  12 rejects")
        counts.setObjectName("counts")
        layout.addWidget(counts)
        self._status_message = load_message
        return bar

    def _stylesheet(self) -> str:
        return f"""
        QWidget#root {{ background: {GROUND}; }}
        QMainWindow, QWidget {{ color: {TEXT}; font-family: 'Segoe UI'; font-size: 12px; }}

        QFrame#toolbar, QFrame#statusbar, QFrame#filmstrip {{
            background: {SURFACE_1};
            border: none;
        }}
        QFrame#toolbar {{ border-bottom: 1px solid {LINE}; }}
        QFrame#statusbar {{ border-top: 1px solid {LINE}; }}
        QFrame#filmstrip {{ border-top: 1px solid {LINE}; }}
        QFrame#rail {{ background: transparent; border: none; }}
        QFrame#vline {{ background: {LINE}; border: none; }}

        QLabel#groupLabel {{ color: {TEXT_MUTE}; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}

        QFrame#segmented {{ background: {SURFACE_3}; border: 1px solid {LINE}; border-radius: 9px; }}
        QFrame#segmented QPushButton {{
            background: transparent; border: none; color: {TEXT_DIM};
            padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500;
        }}
        QFrame#segmented QPushButton:hover {{ background: {SURFACE_HOVER}; color: {TEXT}; }}
        QFrame#segmented QPushButton:checked {{ background: {ACCENT}; color: #ffffff; }}

        QPushButton#toolBtn {{
            background: {SURFACE_3}; border: 1px solid {LINE}; color: {TEXT};
            padding: 7px 13px; border-radius: 8px; font-size: 12px; font-weight: 500;
        }}
        QPushButton#toolBtn:hover {{ background: {SURFACE_HOVER}; border-color: {LINE_STRONG}; }}
        QPushButton#toolBtn:checked {{ background: {ACCENT}; color: #ffffff; border-color: {ACCENT}; }}
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
        }}
        QComboBox#colorCombo {{ min-width: 0px; }}
        QComboBox:hover {{ border-color: {LINE_STRONG}; }}
        QComboBox::drop-down {{ border: none; width: 18px; }}
        QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {LINE_STRONG}; color: {TEXT}; selection-background-color: {ACCENT}; }}

        QLabel#counts {{ color: {TEXT_MUTE}; font-size: 12px; }}
        """


def load_source_pixmap(path: str | None) -> tuple[QPixmap, str]:
    if path:
        image_path = Path(path)
        if image_path.exists():
            reader = QImageReader(str(image_path))
            reader.setAutoTransform(True)
            image = reader.read()
            if not image.isNull():
                return QPixmap.fromImage(image), f"Loaded {image_path}"
    return make_dummy_landscape(QSize(1800, 1100)), "Generated landscape sample (pass --image to test a real file)."


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype the redesigned Studio popout preview.")
    parser.add_argument("--image", help="Optional image to show in the stage.")
    parser.add_argument("--save", help="Render once to this PNG and exit (headless).")
    parser.add_argument(
        "--collapsed",
        action="store_true",
        help="Start with the inspector rail collapsed (headless preview).",
    )
    parser.add_argument(
        "--focus",
        choices=("middle", "second"),
        default="middle",
        help="Where the current frame sits in the filmstrip.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication(sys.argv[:1])
    pixmap, message = load_source_pixmap(args.image)
    window = StudioPreviewPrototype(pixmap, message, film_focus=args.focus)
    if args.collapsed:
        window.inspector_toggle.setChecked(False)
    if args.save:
        window.resize(1200, 800)
        window.show()
        app.processEvents()
        window.grab().save(args.save, "PNG")
        print(f"Saved {args.save}")
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
