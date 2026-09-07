from __future__ import annotations

from functools import lru_cache

import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ..people_search import (
    assign_person_name,
    assign_person_names,
    cluster_face_identities,
    ensure_people_search_schema,
    list_face_identities,
    list_person_clusters,
    set_clusters_ignored,
)
from ..quality.store import ensure_faces_table
from .icons import build_symbol_icon
from aiculler.storage import SQLITE_BUSY_TIMEOUT_MS

_THUMB_PX = 224  # crop resolution; displayed size follows card width
_HOVER_PX = 84
_CARD_W = 196
_TARGET_COL_W = _CARD_W + 18  # card + grid gap, for responsive column count
_MAX_HOVER_FACES = 4
_SWITCH_TRACK = QColor("#575b6c")
_SWITCH_KNOB = QColor("#92abe4")
_DONE_BLUE = "#085dae"
_HEADER_CONTROL_H = 34
_NAME_EDIT_H = 26
_SEGMENT_W = 224
_MIN_THUMB_PX = 96
# U+21B6, matching image_triage/window.py's top-bar undo button.
_UNDO_GLYPH = "\u21b6"


def _elide(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"
_REP_CACHE_MAX = 600
_BODY_MARGIN = 24
_SCROLLBAR_W = 8
_DEFAULT_W = 888
_DEFAULT_H = 1046
_SEGMENT_H = 36
# Track height less its 1px border and the 3px track margins on each side.
# Qt drops border-radius entirely once it exceeds half the widget height,
# so the segment radius must be derived from this, never guessed.
_SEGMENT_BTN_H = _SEGMENT_H - 2 * 1 - 2 * 3
_NAME_WRITE_POOL: QThreadPool | None = None


def _name_write_pool() -> QThreadPool:
    global _NAME_WRITE_POOL
    if _NAME_WRITE_POOL is None:
        _NAME_WRITE_POOL = QThreadPool()
        _NAME_WRITE_POOL.setMaxThreadCount(1)
    return _NAME_WRITE_POOL


# --------------------------------------------------------------------------
# Image helpers
# --------------------------------------------------------------------------
def _circular_pixmap(image: QImage, size: int) -> QPixmap:
    scaled = image.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
    )
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()
    return pixmap


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() * (1 - t) + b.red() * t),
        round(a.green() * (1 - t) + b.green() * t),
        round(a.blue() * (1 - t) + b.blue() * t),
    )


# --------------------------------------------------------------------------
# Icon painting
# --------------------------------------------------------------------------
def _stroke(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _icon_canvas(px: int) -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def _icon_search(color: QColor, px: int = 16) -> QIcon:
    pixmap, painter = _icon_canvas(px)
    painter.setPen(_stroke(color, 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = px * 0.26
    centre = QPointF(px * 0.42, px * 0.42)
    painter.drawEllipse(centre, radius, radius)
    tail = radius * 0.72
    painter.drawLine(QPointF(centre.x() + tail, centre.y() + tail), QPointF(px * 0.85, px * 0.85))
    painter.end()
    return QIcon(pixmap)


def _icon_scan(color: QColor, px: int = 22) -> QIcon:
    """Face-recognition mark: scan brackets around two eyes and a mouth.

    Drawn on a larger canvas than the other glyphs and kept well inside it -
    at 16px the three face marks crowd into a single blob.
    """
    pixmap, painter = _icon_canvas(px)
    painter.setPen(_stroke(color, 1.6))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset, arm = px * 0.14, px * 0.17
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x = inset if sx > 0 else px - inset
        y = inset if sy > 0 else px - inset
        painter.drawLine(QPointF(x, y), QPointF(x + arm * sx, y))
        painter.drawLine(QPointF(x, y), QPointF(x, y + arm * sy))
    for ex in (px * 0.39, px * 0.61):
        painter.drawLine(QPointF(ex, px * 0.36), QPointF(ex, px * 0.45))
    mouth = QPainterPath()
    mouth.moveTo(px * 0.36, px * 0.60)
    mouth.quadTo(px * 0.5, px * 0.70, px * 0.64, px * 0.60)
    painter.drawPath(mouth)
    painter.end()
    return QIcon(pixmap)


def _icon_merge(color: QColor, px: int = 16) -> QIcon:
    """Two paths converging into one - deliberately not a modifier-key glyph."""
    pixmap, painter = _icon_canvas(px)
    painter.setPen(_stroke(color, 1.6))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    mid = px * 0.5
    painter.drawLine(QPointF(px * 0.18, px * 0.16), QPointF(mid, px * 0.48))
    painter.drawLine(QPointF(px * 0.82, px * 0.16), QPointF(mid, px * 0.48))
    painter.drawLine(QPointF(mid, px * 0.48), QPointF(mid, px * 0.84))
    painter.drawLine(QPointF(mid - px * 0.15, px * 0.68), QPointF(mid, px * 0.85))
    painter.drawLine(QPointF(mid + px * 0.15, px * 0.68), QPointF(mid, px * 0.85))
    painter.end()
    return QIcon(pixmap)


def _icon_ignore(color: QColor, px: int = 16) -> QIcon:
    """Circle-slash: this cluster is not a person worth tagging."""
    pixmap, painter = _icon_canvas(px)
    painter.setPen(_stroke(color, 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = px * 0.36
    centre = QPointF(px * 0.5, px * 0.5)
    painter.drawEllipse(centre, radius, radius)
    offset = radius * 0.7071
    painter.drawLine(
        QPointF(centre.x() - offset, centre.y() + offset),
        QPointF(centre.x() + offset, centre.y() - offset),
    )
    painter.end()
    return QIcon(pixmap)


def _icon_clear(color: QColor, px: int = 16) -> QIcon:
    pixmap, painter = _icon_canvas(px)
    painter.setPen(_stroke(color, 1.6))
    low, high = px * 0.26, px * 0.74
    painter.drawLine(QPointF(low, low), QPointF(high, high))
    painter.drawLine(QPointF(high, low), QPointF(low, high))
    painter.end()
    return QIcon(pixmap)


def _icon_pencil(color: QColor, px: int = 14) -> QIcon:
    pixmap, painter = _icon_canvas(px)
    painter.setPen(_stroke(color, 1.4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(px * 0.22, px * 0.78), QPointF(px * 0.70, px * 0.28))
    painter.drawLine(QPointF(px * 0.70, px * 0.28), QPointF(px * 0.84, px * 0.42))
    painter.drawLine(QPointF(px * 0.84, px * 0.42), QPointF(px * 0.36, px * 0.90))
    painter.drawLine(QPointF(px * 0.36, px * 0.90), QPointF(px * 0.16, px * 0.94))
    painter.drawLine(QPointF(px * 0.16, px * 0.94), QPointF(px * 0.22, px * 0.78))
    painter.end()
    return QIcon(pixmap)


def _draw_count_bars(painter: QPainter, left: float, baseline: float, color: QColor) -> None:
    """The little ascending bar chart that precedes a person's photo count."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(color))
    for index, height in enumerate((4.0, 7.0, 10.0)):
        painter.drawRoundedRect(QRectF(left + index * 3.6, baseline - height, 2.4, height), 1.0, 1.0)
    painter.restore()


_VERIFIED_ASSET = Path(__file__).resolve().parent / "assets" / "verified.png"


@lru_cache(maxsize=8)
def _verified_pixmap(edge: int) -> QPixmap:
    """The verified mark at one device-pixel size, or a null pixmap if missing."""
    source = QPixmap(str(_VERIFIED_ASSET))
    if source.isNull():
        return QPixmap()
    return source.scaled(
        edge,
        edge,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class _VerifiedBadge(QWidget):
    """Marks a person whose name a human typed, rather than one the clusterer guessed."""

    def __init__(self, size: int, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setToolTip("Name confirmed by you")

    def paintEvent(self, event) -> None:  # type: ignore[override]
        # Rendered at device resolution so the mark stays crisp on a HiDPI screen.
        ratio = self.devicePixelRatioF()
        pixmap = _verified_pixmap(max(1, round(self.width() * ratio)))
        if pixmap.isNull():
            return
        pixmap.setDevicePixelRatio(ratio)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()


class _ToggleSwitch(QAbstractButton):
    """iOS-style switch; replaces the tick box on "Include single-photo faces"."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 21)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._travel = 0.0
        # One oval colour in both states: the knob's position carries on/off.
        self._track_off = QColor("#575b6c")
        self._track_on = QColor("#575b6c")
        self._knob = QColor("#92abe4")
        self._animation = QPropertyAnimation(self, b"travel", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._animate)

    def set_colors(self, track_off: QColor, track_on: QColor, knob: QColor) -> None:
        self._track_off, self._track_on, self._knob = track_off, track_on, knob
        self.update()

    def _animate(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        self._animation.stop()
        # Toggled before the dialog is on screen (restoring a saved filter, say):
        # there is no frame to animate into, so snap or the knob paints the lie.
        if not self.isVisible():
            self._set_travel(target)
            return
        self._animation.setStartValue(self._travel)
        self._animation.setEndValue(target)
        self._animation.start()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._set_travel(1.0 if self.isChecked() else 0.0)

    def _get_travel(self) -> float:
        return self._travel

    def _set_travel(self, value: float) -> None:
        self._travel = float(value)
        self.update()

    travel = Property(float, _get_travel, _set_travel)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = self.height() / 2
        # The oval is an outline, not a fill: bluegray ring, transparent inside.
        ring = _blend(self._track_off, self._track_on, self._travel)
        pen_w = 1.6
        painter.setPen(_stroke(ring, pen_w))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        half = pen_w / 2.0
        painter.drawRoundedRect(
            QRectF(half, half, self.width() - pen_w, self.height() - pen_w),
            radius - half,
            radius - half,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        inset = 4.0
        knob_r = radius - inset
        span = self.width() - 2 * (knob_r + inset)
        centre = QPointF(inset + knob_r + span * self._travel, radius)
        painter.setBrush(QBrush(self._knob))
        painter.drawEllipse(centre, knob_r, knob_r)
        painter.end()


Bbox = tuple[float, float, float, float]


def rank_faces(faces: list[dict]) -> list[dict]:
    """Order candidate faces best-first for representative selection.

    Uses the signals we actually store — detector confidence, face size, and
    eye sharpness — to avoid tiny / low-confidence / soft crops. (Pose and
    exposure are not persisted, so they cannot factor in yet.)
    """
    if not faces:
        return []
    areas = [max(1.0, (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1])) for f in faces]
    sharps = [float(f.get("sharp") or 0.0) for f in faces]
    max_area = max(areas)
    max_sharp = max(sharps) or 1.0
    scored: list[tuple[float, dict]] = []
    for face, area, sharp in zip(faces, areas, sharps):
        score = 0.45 * float(face["det"]) + 0.40 * (area / max_area) + 0.15 * (sharp / max_sharp)
        scored.append((score, face))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [face for _score, face in scored]


@dataclass
class _Person:
    name: str
    cluster_ids: list[int]
    face_count: int
    original_name: str = ""
    rep_key: int = 0  # stable key for thumbnail routing
    rep_face: tuple[str, Bbox] | None = None
    extra_faces: list[tuple[str, Bbox]] = field(default_factory=list)

    @property
    def named(self) -> bool:
        return bool(self.name.strip())


def _merge_people_stably(existing: list[_Person], current: list[_Person]) -> list[_Person]:
    """Keep known people in place and append newly discovered people."""
    remaining = list(current)
    ordered: list[_Person] = []
    for previous in existing:
        match = next(
            (
                person
                for person in remaining
                if set(previous.cluster_ids).intersection(person.cluster_ids)
                or (
                    previous.named
                    and person.named
                    and previous.name.casefold() == person.name.casefold()
                )
            ),
            None,
        )
        if match is None:
            continue
        remaining.remove(match)
        match.rep_key = previous.rep_key
        ordered.append(match)
    ordered.extend(remaining)
    return ordered


# --------------------------------------------------------------------------
# Async face cropping (representative + hover previews)
# --------------------------------------------------------------------------
class _CropSignals(QObject):
    loaded = Signal(int, int, QImage)  # key, slot_index, circular crop
    finished = Signal()


class _CropTask(QRunnable):
    def __init__(self, jobs: list[tuple[int, int, str, Bbox]], size: int, cache_dir: str):
        super().__init__()
        self.jobs = jobs
        self.size = size
        self.cache_dir = cache_dir
        self.signals = _CropSignals()
        self.setAutoDelete(True)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            try:
                import numpy as np
                from PIL import Image

                from aiculler.features import PreviewExtractor
            except Exception:
                return
            # A shared, persistent cache dir means each source image is decoded to a
            # preview exactly once and reused across representative + hover crops,
            # instead of re-decoding (RAW/large) files on every hover.
            extractor = PreviewExtractor(self.cache_dir)
            for key, slot, source_path, bbox in self.jobs:
                if self._cancelled:
                    return
                try:
                    preview_path, _ = extractor.extract(Path(source_path))
                    with Image.open(preview_path) as opened:
                        image = opened.convert("RGB")
                    width, height = image.size
                    x1, y1, x2, y2 = bbox
                    pad_x = (x2 - x1) * 0.3
                    pad_y = (y2 - y1) * 0.3
                    left, top = max(0, int(x1 - pad_x)), max(0, int(y1 - pad_y))
                    right, bottom = min(width, int(x2 + pad_x)), min(height, int(y2 + pad_y))
                    if right <= left or bottom <= top:
                        continue
                    crop = image.crop((left, top, right, bottom)).resize(
                        (self.size, self.size), Image.Resampling.BILINEAR
                    )
                    arr = np.asarray(crop, dtype=np.uint8)
                    qimage = QImage(
                        arr.tobytes(), self.size, self.size, 3 * self.size, QImage.Format.Format_RGB888
                    ).copy()
                    self.signals.loaded.emit(key, slot, qimage)
                except Exception:
                    continue
        finally:
            self.signals.finished.emit()


class _ClusterSignals(QObject):
    finished = Signal()
    failed = Signal(str)


class _ClusterTask(QRunnable):
    """Re-cluster faces off the UI thread so Rescan Faces shows a live spinner."""

    def __init__(self, db_path: str, identity_model: str) -> None:
        super().__init__()
        self.db_path = db_path
        self.identity_model = identity_model
        self.signals = _ClusterSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            connection = sqlite3.connect(
                self.db_path,
                timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            try:
                ensure_faces_table(connection)
                ensure_people_search_schema(connection)
                cluster_face_identities(connection, identity_model=self.identity_model)
                connection.commit()
            finally:
                connection.close()
        except Exception as exc:  # pragma: no cover - defensive
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit()


class _NameSaveSignals(QObject):
    finished = Signal(object)
    failed = Signal(object, str)


class _NameSaveTask(QRunnable):
    """Persist a name without ever waiting for SQLite on the UI thread."""

    def __init__(self, db_path: str, cluster_ids: list[int], name: str, previous_name: str, rep_key: int) -> None:
        super().__init__()
        self.db_path = db_path
        self.cluster_ids = list(cluster_ids)
        self.name = name
        self.previous_name = previous_name
        self.rep_key = rep_key
        self.signals = _NameSaveSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            connection = sqlite3.connect(
                self.db_path,
                timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
            )
            connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            try:
                assign_person_names(connection, self.cluster_ids, self.name)
            finally:
                connection.close()
        except Exception as exc:  # pragma: no cover - surfaced to the dialog
            self.signals.failed.emit(self, str(exc))
            return
        self.signals.finished.emit(self)


class _HoverPreview(QFrame):
    """Small popover showing a person's next-best faces, to verify a cluster."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setObjectName("hoverPreview")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self._slots: list[QLabel] = []

    def show_for(self, count: int) -> None:
        for label in self._slots:
            label.setParent(None)
            label.deleteLater()
        self._slots = []
        layout = self.layout()
        for _ in range(count):
            label = QLabel(self)
            label.setFixedSize(_HOVER_PX, _HOVER_PX)
            layout.addWidget(label)
            self._slots.append(label)

    def set_face(self, slot: int, image: QImage) -> None:
        if 0 <= slot < len(self._slots):
            self._slots[slot].setPixmap(_circular_pixmap(image, _HOVER_PX))


# --------------------------------------------------------------------------
# Person card
# --------------------------------------------------------------------------
class _NameEdit(QLineEdit):
    escaped = Signal()
    submitted = Signal()
    focused = Signal()

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        super().focusInEvent(event)
        self.focused.emit()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _BarsGlyph(QWidget):
    """The ascending-bars mark that sits before a person's photo count."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 14)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._color = QColor("#8e8e93")

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _draw_count_bars(painter, 0.5, self.height() - 2.0, self._color)
        painter.end()


class _PersonCard(QFrame):
    select_requested = Signal(object, object)  # card, modifiers
    name_committed = Signal(object, str, bool)  # card, text, via_enter
    edit_started = Signal(object)
    hover_changed = Signal(object, bool)  # card, entered
    metrics_changed = Signal()  # the card's preferred height changed
    context_menu_requested = Signal(object, object)  # card, global position

    def __init__(self, person: _Person, parent=None) -> None:
        super().__init__(parent)
        self.person = person
        self._selected = False
        self.setObjectName("personCard")
        self.setMinimumWidth(_CARD_W)
        # Expand to fill the column so the grid justifies edge-to-edge; the
        # thumbnail/name stay centred inside the wider card.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setProperty("selected", False)
        self.setProperty("focused", False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(9)

        self.thumb = QLabel(self)
        self._thumb_image: QImage | None = None
        self._thumb_px = _MIN_THUMB_PX
        self._thumb_bg = "#48484a"
        self._thumb_fg = "#8e8e93"
        self.thumb.setFixedSize(self._thumb_px, self._thumb_px)
        self.thumb.setObjectName("personThumb")
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setText(person.name.strip()[:1].upper() if person.named else "")
        layout.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignHCenter)

        # Name control. A named person reads as plain text plus a confirmed
        # badge; an unnamed one shows the input up front, so the whole grid can
        # be filled in without a click per card.
        self._name_stack = QStackedLayout()
        self._name_stack.setContentsMargins(0, 0, 0, 0)

        named_row = QWidget(self)
        named_layout = QHBoxLayout(named_row)
        named_layout.setContentsMargins(0, 0, 0, 0)
        named_layout.setSpacing(5)
        named_layout.addStretch(1)
        self.name_button = QPushButton(named_row)
        self.name_button.setObjectName("nameButton")
        self.name_button.setFlat(True)
        self.name_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name_button.clicked.connect(self.begin_edit)
        named_layout.addWidget(self.name_button)
        self.badge = _VerifiedBadge(15, named_row)
        named_layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        named_layout.addStretch(1)

        self.name_edit = _NameEdit(self)
        self.name_edit.setObjectName("nameEdit")
        self.name_edit.setPlaceholderText("Name this person...")
        self.name_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.name_edit.setClearButtonEnabled(False)
        self._pencil_action = self.name_edit.addAction(
            QIcon(), QLineEdit.ActionPosition.TrailingPosition
        )
        self._pencil_action.triggered.connect(self.begin_edit)
        self.name_edit.submitted.connect(lambda: self._commit(via_enter=True))
        self.name_edit.editingFinished.connect(lambda: self._commit(via_enter=False))
        self.name_edit.escaped.connect(self._cancel_edit)
        self.name_edit.focused.connect(self._on_edit_focused)
        # A floor, not a fixed size: at larger fonts or DPI a hard height
        # clips the input's bottom border.
        self.name_edit.setMinimumHeight(_NAME_EDIT_H)

        # Inset the input a few px so it does not run the full card width.
        edit_row = QWidget(self)
        edit_layout = QHBoxLayout(edit_row)
        edit_layout.setContentsMargins(7, 0, 7, 0)
        edit_layout.setSpacing(0)
        edit_layout.addWidget(self.name_edit)

        name_host = QWidget(self)
        self._name_stack.addWidget(named_row)
        self._name_stack.addWidget(edit_row)
        name_host.setLayout(self._name_stack)
        layout.addWidget(name_host)

        count_row = QWidget(self)
        count_layout = QHBoxLayout(count_row)
        count_layout.setContentsMargins(0, 0, 0, 0)
        count_layout.setSpacing(6)
        count_layout.addStretch(1)
        self.bars = _BarsGlyph(count_row)
        count_layout.addWidget(self.bars, 0, Qt.AlignmentFlag.AlignVCenter)
        self.count_label = QLabel(count_row)
        self.count_label.setObjectName("personCount")
        count_layout.addWidget(self.count_label)
        count_layout.addStretch(1)
        layout.addWidget(count_row)

        self._editing = False
        self._refresh_name()

    def set_chrome_colors(self, *, muted: QColor, thumb_bg: QColor) -> None:
        self.bars.set_color(muted)
        self._pencil_action.setIcon(_icon_pencil(muted))
        self._thumb_bg = thumb_bg.name()
        self._thumb_fg = muted.name()
        self._apply_thumb_style()

    def _apply_thumb_style(self) -> None:
        self.thumb.setStyleSheet(
            f"background: {self._thumb_bg}; border-radius: {self._thumb_px // 2}px;"
            f" color: {self._thumb_fg}; font-size: 30px; font-weight: 600;"
        )

    # -- name control ------------------------------------------------------
    def _refresh_name(self) -> None:
        named = self.person.named
        if named:
            self.name_button.setText(self.person.name)
            self._name_stack.setCurrentIndex(0)
        elif not self._editing:
            self.name_edit.setText("")
            self._name_stack.setCurrentIndex(1)
        self.badge.setVisible(named)
        merged = len(self.person.cluster_ids)
        word = "photo" if self.person.face_count == 1 else "photos"
        self.count_label.setText(f"{self.person.face_count} {word}")
        if merged > 1:
            self.count_label.setToolTip(f"{self.person.face_count} photos from {merged} merged face groups")
        else:
            self.count_label.setToolTip("")

    def begin_edit(self) -> None:
        self._editing = True
        self.name_edit.setText(self.person.name)
        self._name_stack.setCurrentIndex(1)
        self.name_edit.setFocus()
        self.name_edit.selectAll()
        self.edit_started.emit(self)

    def _on_edit_focused(self) -> None:
        # An unnamed card shows its input permanently, so "editing" has to mean
        # "the input holds focus" - otherwise the dialog's refresh timer, which
        # pauses during edits, would never run again.
        if not self._editing:
            self._editing = True
            self.edit_started.emit(self)

    def _commit(self, *, via_enter: bool) -> None:
        if not self._editing:
            return
        self._editing = False
        text = self.name_edit.text().strip()
        if text != self.person.original_name:
            self.name_committed.emit(self, text, via_enter)
        else:
            self._refresh_name()
            if via_enter:
                self.name_committed.emit(self, text, True)  # allow advance even with no change

    def _cancel_edit(self) -> None:
        self._editing = False
        self._refresh_name()
        self.setFocus()

    def apply_name(self, name: str) -> None:
        self.person.name = name
        self.person.original_name = name
        self._refresh_name()

    # -- selection / focus visuals ----------------------------------------
    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.setProperty("selected", selected)
        self._repolish()

    def is_selected(self) -> bool:
        return self._selected

    def set_keyboard_focus(self, focused: bool) -> None:
        self.setProperty("focused", focused)
        self._repolish()
        if focused:
            self.setFocus()

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_thumbnail(self, image: QImage) -> None:
        self._thumb_image = image
        self.thumb.setText("")
        self.thumb.setPixmap(_circular_pixmap(image, self._thumb_px))

    # -- square tiles ------------------------------------------------------
    def _chrome_height(self) -> int:
        """Everything in the card that is not the face."""
        layout = self.layout()
        margins = layout.contentsMargins()
        rows = margins.top() + margins.bottom() + 2 * layout.spacing()
        for index in (1, 2):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                rows += widget.sizeHint().height()
        return rows

    def _fit_thumb(self) -> None:
        """Size the face so the card comes out square at whatever width it got.

        The chrome below the photo is a fixed height, so letting the face take
        the remainder keeps every card square and the side margins constant,
        at three columns or five.
        """
        # The frame border sits outside the layout, and it is thicker when the
        # card is selected - subtract the real thing so the outer box stays
        # square either way, instead of jumping 2px on selection.
        border = max(0, self.height() - self.contentsRect().height())
        side = max(_MIN_THUMB_PX, self.width() - self._chrome_height() - border)
        if side == self._thumb_px:
            return
        self._thumb_px = side
        self.thumb.setFixedSize(side, side)
        self._apply_thumb_style()
        if self._thumb_image is not None:
            self.thumb.setPixmap(_circular_pixmap(self._thumb_image, side))
        # The card is now taller, so the scroll area has to be told again how
        # much room the grid needs - otherwise it squeezes the rows back down.
        self.metrics_changed.emit()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._fit_thumb()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._selected:
            return
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = 11
        cx = self.width() - 12 - r
        cy = 14 + r
        painter.setBrush(QBrush(accent))
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Base), 2))
        painter.drawEllipse(QPoint(cx, cy), r, r)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.HighlightedText), 2))
        painter.drawLine(cx - 4, cy, cx - 1, cy + 4)
        painter.drawLine(cx - 1, cy + 4, cx + 5, cy - 4)
        painter.end()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self.hover_changed.emit(self, True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.hover_changed.emit(self, False)
        super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        # The name input keeps its own editing menu; everywhere else on the
        # card opens the person menu.
        self.context_menu_requested.emit(self, event.globalPos())
        event.accept()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        # Clicks on the thumbnail (not the name control) select the card.
        if self.thumb.geometry().contains(event.position().toPoint()):
            self.select_requested.emit(self, event.modifiers())
        super().mousePressEvent(event)


# --------------------------------------------------------------------------
# Dialog
# --------------------------------------------------------------------------
class PeopleSearchDialog(QDialog):
    def __init__(self, db_path: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tag People")
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        # Sized so the default 3-column grid shows three full rows without
        # scrolling: square 265px cards + 18px gaps + header/filter/footer chrome.
        # Clamped to the screen so this does not overflow a 1080p display.
        target_height = _DEFAULT_H
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            target_height = min(target_height, int(screen.availableGeometry().height() * 0.96))
        self.resize(_DEFAULT_W, target_height)
        self.setMinimumSize(620, 560)
        self._db_path = Path(db_path)
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            self._db_path,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        ensure_faces_table(self._connection)
        ensure_people_search_schema(self._connection)

        # Persistent crop cache: each source image is decoded to a preview once
        # and reused for both representative thumbnails and hover previews.
        self._crop_cache_dir = tempfile.mkdtemp(prefix="people_crops_")
        # Representative thumbnails and hover previews get separate single-thread
        # pools so a large representative-crop backlog can never block a hover.
        self._crop_pool = QThreadPool(self)
        self._crop_pool.setMaxThreadCount(1)
        self._hover_pool = QThreadPool(self)
        self._hover_pool.setMaxThreadCount(1)
        self._hover_cache: dict[int, list[QImage]] = {}
        # Decoded representative faces, so re-showing a person never re-decodes.
        self._rep_cache: dict[int, QImage] = {}
        self._active_crop_task: _CropTask | None = None
        self._pending_rep_people: dict[int, _Person] = {}
        self._active_hover_task: _CropTask | None = None
        self._active_cluster_task: _ClusterTask | None = None
        self._name_save_tasks: set[_NameSaveTask] = set()
        self._scan_progress: QProgressDialog | None = None
        self._cards: list[_PersonCard] = []
        self._card_by_key: dict[int, _PersonCard] = {}
        self._people: list[_Person] = []
        self._focus_index = -1
        self._current_cols = 3
        self._hover_card: _PersonCard | None = None
        self._ignored_undo: list[int] = []
        # Set when the user asks to see one person's photos. The caller reads
        # these after exec() and filters the grid by the cluster's own paths -
        # by path, not by name, so unnamed faces work too.
        self.requested_person_label: str = ""
        self.requested_person_paths: tuple[str, ...] = ()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Everything above the docked footer band lives in this inset column.
        content = QWidget(self)
        body = QVBoxLayout(content)
        body.setContentsMargins(_BODY_MARGIN, 20, _BODY_MARGIN, 14)
        self._body_layout = body
        body.setSpacing(12)
        root.addWidget(content, 1)

        # -- header: title + stats, search, scan control --------------------
        header = QHBoxLayout()
        header.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self.title_label = QLabel("Tag People", content)
        self.title_label.setObjectName("peopleTitle")
        title_col.addWidget(self.title_label)
        self.stats_label = QLabel("", content)
        self.stats_label.setObjectName("peopleStats")
        title_col.addWidget(self.stats_label)
        self.progress = QProgressBar(content)
        self.progress.setObjectName("scanProgress")
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.setVisible(False)
        title_col.addWidget(self.progress)
        title_col.addStretch(1)
        header.addLayout(title_col, 1)

        self.search_edit = QLineEdit(content)
        self.search_edit.setObjectName("searchEdit")
        self.search_edit.setPlaceholderText("Search")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(240)
        self._search_action = self.search_edit.addAction(
            QIcon(), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search_edit.textChanged.connect(self._on_search_changed)

        self.scan_button = QPushButton("Rescan Faces", content)
        self.scan_button.setObjectName("scanButton")
        self.scan_button.clicked.connect(self._rescan)

        # Both controls live in a host whose top margin is set from the title's
        # font metrics, so their top edges line up with the title's capitals
        # rather than with the invisible top of its text box.
        controls_host = QWidget(content)
        self._controls_layout = QHBoxLayout(controls_host)
        self._controls_layout.setContentsMargins(0, 0, 0, 0)
        self._controls_layout.setSpacing(12)
        self._controls_layout.addWidget(self.search_edit)
        self._controls_layout.addWidget(self.scan_button)
        header.addWidget(controls_host, 0, Qt.AlignmentFlag.AlignTop)
        self._header_controls = (self.search_edit, self.scan_button)
        self._header_row = header
        body.addLayout(header)

        # -- filter row ----------------------------------------------------
        filters = QHBoxLayout()
        filters.setSpacing(10)
        self.segment_track = QFrame(content)
        self.segment_track.setObjectName("segTrack")
        track_layout = QHBoxLayout(self.segment_track)
        track_layout.setContentsMargins(3, 3, 3, 3)
        track_layout.setSpacing(0)
        self.filter_group = QButtonGroup(self)
        self.filter_all = QPushButton("All", self.segment_track)
        self.filter_unnamed = QPushButton("Unnamed", self.segment_track)
        for i, btn in enumerate((self.filter_all, self.filter_unnamed)):
            btn.setObjectName("segButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(_SEGMENT_BTN_H)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.filter_group.addButton(btn, i)
            track_layout.addWidget(btn, 1)
        self.filter_all.setChecked(True)
        self.filter_group.idClicked.connect(lambda _id: self._reload())
        self.segment_track.setFixedHeight(_SEGMENT_H)
        self.segment_track.setFixedWidth(_SEGMENT_W)
        filters.addWidget(self.segment_track, 0, Qt.AlignmentFlag.AlignLeft)
        filters.addStretch(1)
        self.include_singles = _ToggleSwitch(content)
        self.include_singles.toggled.connect(self._reload)
        filters.addWidget(self.include_singles, 0, Qt.AlignmentFlag.AlignVCenter)
        self.singles_label = QLabel("Include single-photo faces", content)
        self.singles_label.setObjectName("switchLabel")
        filters.addWidget(self.singles_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._filter_row = filters
        body.addLayout(filters)

        # -- grid (responsive, centred) -----------------------------------
        self.scroll = QScrollArea(content)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Re-sync the right gutter whenever the vertical scrollbar appears or
        # disappears (its range changes exactly then).
        self.scroll.verticalScrollBar().rangeChanged.connect(lambda *_: self._schedule_gutter_sync())
        self._grid_host = QWidget()
        outer = QVBoxLayout(self._grid_host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # The grid fills the full viewport width; equal column stretch (set in
        # _relayout_grid) justifies the cards edge-to-edge so both the left and
        # right edges line up with the header and filter rows.
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(18)
        outer.addLayout(self._grid)
        outer.addStretch(1)
        self.scroll.setWidget(self._grid_host)
        body.addWidget(self.scroll, 1)

        self.empty_label = QLabel("", content)
        self.empty_label.setObjectName("peopleEmpty")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setVisible(False)
        body.addWidget(self.empty_label, 1)

        # -- docked footer band --------------------------------------------
        self.footer_band = QFrame(self)
        self.footer_band.setObjectName("footerBand")
        band = QVBoxLayout(self.footer_band)
        band.setContentsMargins(24, 12, 24, 16)
        band.setSpacing(10)

        # Contextual actions, shown only while at least one person is selected.
        self.action_row = QWidget(self.footer_band)
        actions = QHBoxLayout(self.action_row)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(10)
        actions.addStretch(1)
        self.merge_button = QPushButton("Merge Selected", self.action_row)
        self.merge_button.setObjectName("actionButton")
        self.merge_button.clicked.connect(self._merge_selected)
        actions.addWidget(self.merge_button)
        self.ignore_button = QPushButton("Ignore Selected", self.action_row)
        self.ignore_button.setObjectName("actionButton")
        self.ignore_button.clicked.connect(self._ignore_selected)
        actions.addWidget(self.ignore_button)
        self.clear_button = QPushButton("Clear Selected", self.action_row)
        self.clear_button.setObjectName("actionButton")
        self.clear_button.clicked.connect(self._clear_selection)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        self.action_row.setVisible(False)
        band.addWidget(self.action_row)

        # Ignoring is reversible, but only if the way back is offered here --
        # an ignored cluster is otherwise gone from every view in the dialog.
        self.undo_row = QWidget(self.footer_band)
        undo_layout = QHBoxLayout(self.undo_row)
        undo_layout.setContentsMargins(0, 0, 0, 0)
        undo_layout.setSpacing(10)
        undo_layout.addStretch(1)
        self.undo_label = QLabel("", self.undo_row)
        self.undo_label.setObjectName("undoLabel")
        undo_layout.addWidget(self.undo_label)
        self.undo_button = QPushButton("Undo", self.undo_row)
        self.undo_button.setObjectName("actionButton")
        self.undo_button.clicked.connect(self._undo_ignore)
        undo_layout.addWidget(self.undo_button)
        undo_layout.addStretch(1)
        self.undo_row.setVisible(False)
        band.addWidget(self.undo_row)

        self.done_button = QPushButton("DONE", self.footer_band)
        self.done_button.setObjectName("doneButton")
        self.done_button.setDefault(False)
        self.done_button.setAutoDefault(False)
        self.done_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done_button.clicked.connect(self.accept)
        done_row = QHBoxLayout()
        done_row.setContentsMargins(0, 0, 0, 0)
        done_row.addStretch(1)
        done_row.addWidget(self.done_button)
        done_row.addStretch(1)
        band.addLayout(done_row)
        root.addWidget(self.footer_band)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._populate_cards)

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(400)
        self._hover_timer.timeout.connect(self._show_hover_preview)
        self._hover_popover = _HoverPreview(self)

        # The face index commits progressively on another connection. Keep an
        # already-open People window in sync instead of requiring it to be
        # closed and reopened after the background task finishes.
        self._database_revision: tuple[object, ...] | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1500)
        self._refresh_timer.timeout.connect(self._refresh_if_database_changed)

        self.setStyleSheet(self._stylesheet())
        self._apply_chrome_icons()
        self._sync_header_control_heights()
        self._reload()
        self._database_revision = self._read_database_revision()
        self._refresh_timer.start()

    # -- styling -----------------------------------------------------------
    def _stylesheet(self) -> str:
        pal = self.palette()
        base = pal.color(QPalette.ColorRole.Base)
        window = pal.color(QPalette.ColorRole.Window)
        text = pal.color(QPalette.ColorRole.Text)
        mid = pal.color(QPalette.ColorRole.Mid)
        hl = pal.color(QPalette.ColorRole.Highlight)
        on_hl = pal.color(QPalette.ColorRole.HighlightedText).name()
        muted = pal.color(QPalette.ColorRole.PlaceholderText).name()
        subtle = _blend(base, text, 0.10).name()  # faint normal border
        tint = QColor(hl.red(), hl.green(), hl.blue(), 28).name(QColor.NameFormat.HexArgb)
        accent = hl.name()
        # Each card carries a soft top-left-to-bottom-right sheen so the grid
        # reads as a set of lit surfaces rather than flat rectangles.
        card_top = _blend(base, text, 0.055).name()
        card_bottom = _blend(base, window, 0.55).name()
        card_gradient = (
            f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {card_top}, stop:1 {card_bottom})"
        )
        card_hover_top = _blend(base, text, 0.10).name()
        card_hover_gradient = (
            f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {card_hover_top}, stop:1 {card_bottom})"
        )
        band = _blend(window, QColor(0, 0, 0), 0.22).name()
        # Translucent so the bar reads as a hint rather than a fixture.
        handle = QColor(text.red(), text.green(), text.blue(), 48).name(QColor.NameFormat.HexArgb)
        handle_hover = QColor(text.red(), text.green(), text.blue(), 96).name(
            QColor.NameFormat.HexArgb
        )
        field = _blend(base, text, 0.07).name()
        return f"""
            QLabel#peopleTitle {{ color: {text.name()}; font-size: 20px; font-weight: 700; }}
            QLabel#peopleStats {{ color: {muted}; font-size: 12px; }}
            QLabel#peopleEmpty {{ color: {muted}; font-size: 13px; }}
            QLabel#switchLabel {{ color: {text.name()}; font-size: 12px; }}
            QProgressBar#scanProgress {{ background: {mid.name()}; border: none; border-radius: 1px; }}
            QProgressBar#scanProgress::chunk {{ background: {accent}; border-radius: 1px; }}

            QLineEdit#searchEdit {{
                background: {field}; border: 1px solid {subtle}; border-radius: 9px;
                padding: 0px 10px; color: {text.name()}; font-size: 14px;
            }}
            QLineEdit#searchEdit:focus {{ border-color: {accent}; }}

            QFrame#segTrack {{ background: {field}; border: 1px solid {subtle}; border-radius: 18px; }}
            QPushButton#segButton {{
                padding: 6px 18px; border: 1px solid transparent;
                border-radius: {_SEGMENT_BTN_H // 2}px;
                background-color: transparent; color: {muted}; font-size: 12px; min-width: 0px;
            }}
            QPushButton#segButton:hover {{ color: {text.name()}; }}
            QPushButton#segButton:checked {{
                color: {text.name()}; font-weight: 600;
                border: 1px solid transparent;
                border-radius: {_SEGMENT_BTN_H // 2}px;
                background-color: {_blend(base, text, 0.16).name()};
            }}

            QFrame#personCard {{
                background: {card_gradient}; border: 1px solid {subtle}; border-radius: 14px;
            }}
            QFrame#personCard:hover {{
                background: {card_hover_gradient}; border-color: {_blend(base, hl, 0.5).name()};
            }}
            QFrame#personCard[focused="true"] {{ border: 1px dashed {accent}; }}
            QFrame#personCard[selected="true"] {{ border: 2px solid {accent}; }}
            QPushButton#nameButton {{ border: none; background: transparent; padding: 2px 2px;
                font-size: 15px; font-weight: 600; color: {text.name()}; min-width: 0px; text-align: center; }}
            QLineEdit#nameEdit {{
                background: {field}; border: 1px solid {subtle}; border-radius: 7px;
                color: {text.name()}; font-size: 12px; padding: 2px 8px;
            }}
            QLineEdit#nameEdit:focus {{ border-color: {accent}; }}
            QLabel#personCount {{ color: {muted}; font-size: 12px; }}

            QFrame#hoverPreview {{ background: {base.name()}; border: 1px solid {accent}; border-radius: 12px; }}

            QFrame#footerBand {{ background: {band}; border: none;
                border-top: 1px solid {subtle}; }}
            QLabel#undoLabel {{ color: {muted}; font-size: 12px; }}

            QPushButton {{ padding: 8px 18px; min-width: 92px; border-radius: 8px;
                border: 1px solid {mid.name()}; background: transparent; color: {text.name()}; font-size: 13px; }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton:disabled {{ color: {muted}; border-color: {subtle}; }}
            QPushButton#actionButton {{
                background: {field}; border: 1px solid {subtle}; padding: 8px 16px; font-size: 13px;
            }}
            QPushButton#actionButton:hover {{ border-color: {accent}; }}
            QPushButton#doneButton {{
                background: {_DONE_BLUE}; color: #ffffff; border: none; font-weight: 700;
                font-size: 13px; letter-spacing: 1px; padding: 9px 18px; border-radius: 9px;
                min-width: 150px; max-width: 150px;
            }}
            QPushButton#doneButton:hover {{
                background: {_blend(QColor(_DONE_BLUE), QColor("#ffffff"), 0.14).name()};
            }}
            QPushButton#scanButton {{ padding: 7px 14px; }}

            QScrollArea {{ background: transparent; }}
            QScrollBar:vertical {{
                background: transparent; width: {_SCROLLBAR_W}px; margin: 0px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background-color: {handle}; border: none;
                border-radius: {_SCROLLBAR_W // 2}px; min-height: 40px;
            }}
            QScrollBar::handle:vertical:hover {{ background-color: {handle_hover}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """

    def _sync_header_control_heights(self) -> None:
        """Match the search field to the Rescan button exactly.

        Taken from whichever control naturally wants more room once the
        stylesheet is applied, so the pair stays identical at any font or DPI
        instead of both being squeezed to a hard-coded number.
        """
        height = max(w.sizeHint().height() for w in self._header_controls)
        height = max(height, _HEADER_CONTROL_H)
        for widget in self._header_controls:
            widget.setFixedHeight(height)
        # A label's box starts above its capitals by the font's ascent-to-cap
        # gap; drop the controls by exactly that so the two tops agree.
        # Polish first: before that the label still reports the inherited app
        # font, not the larger one the stylesheet gives it.
        self.title_label.ensurePolished()
        metrics = QFontMetrics(self.title_label.font())
        cap_gap = max(0, metrics.ascent() - metrics.capHeight())
        self._controls_layout.setContentsMargins(0, cap_gap, 0, 0)

    def _apply_chrome_icons(self) -> None:
        pal = self.palette()
        text = pal.color(QPalette.ColorRole.Text)
        muted = pal.color(QPalette.ColorRole.PlaceholderText)
        self._search_action.setIcon(_icon_search(muted, 18))
        self.scan_button.setIcon(_icon_scan(text))
        self.scan_button.setIconSize(QSize(20, 20))
        self.merge_button.setIcon(_icon_merge(text))
        self.ignore_button.setIcon(_icon_ignore(text))
        self.clear_button.setIcon(_icon_clear(text))
        # The same glyph the main window's top-bar undo button uses.
        self.undo_button.setIcon(build_symbol_icon(_UNDO_GLYPH, text, pixel_size=20, font_size=16))
        self.undo_button.setIconSize(QSize(20, 20))
        self.include_singles.set_colors(_SWITCH_TRACK, _SWITCH_TRACK, _SWITCH_KNOB)

    def _card_chrome(self, card: _PersonCard) -> None:
        pal = self.palette()
        card.set_chrome_colors(
            muted=pal.color(QPalette.ColorRole.PlaceholderText),
            thumb_bg=pal.color(QPalette.ColorRole.Mid),
        )


    # -- lifecycle ---------------------------------------------------------
    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._teardown()
        super().closeEvent(event)

    def reject(self) -> None:  # type: ignore[override]
        self._teardown()
        super().reject()

    def accept(self) -> None:  # type: ignore[override]
        self._teardown()
        super().accept()

    def _teardown(self) -> None:
        self._refresh_timer.stop()
        for task in (self._active_crop_task, self._active_hover_task):
            if task is not None:
                task.cancel()
        self._active_crop_task = None
        self._active_hover_task = None
        self._pending_rep_people.clear()
        self._crop_pool.waitForDone(2000)
        self._hover_pool.waitForDone(2000)
        if self._scan_progress is not None:
            self._scan_progress.close()
            self._scan_progress = None
        self._hover_popover.hide()
        cache_dir = getattr(self, "_crop_cache_dir", None)
        if cache_dir:
            import shutil

            shutil.rmtree(cache_dir, ignore_errors=True)
            self._crop_cache_dir = ""
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()

    # -- data --------------------------------------------------------------
    def _faces_by_cluster(self) -> dict[int, list[dict]]:
        rows = self._connection.execute(
            """
            SELECT image_faces.cluster_id AS cid, images.source_path AS sp,
                   image_faces.x1, image_faces.y1, image_faces.x2, image_faces.y2,
                   image_faces.det_score AS det, image_faces.eye_sharpness AS sharp
            FROM image_faces JOIN images ON images.id = image_faces.image_id
            WHERE image_faces.cluster_id IS NOT NULL
            """
        ).fetchall()
        by_cluster: dict[int, list[dict]] = {}
        for row in rows:
            by_cluster.setdefault(int(row["cid"]), []).append(
                {
                    "source": str(row["sp"]),
                    "bbox": (float(row["x1"]), float(row["y1"]), float(row["x2"]), float(row["y2"])),
                    "det": float(row["det"]),
                    "sharp": row["sharp"],
                }
            )
        return by_cluster

    def _build_people(self) -> list[_Person]:
        if self._connection is None:
            return []
        clusters = list_person_clusters(self._connection)
        faces_by_cluster = self._faces_by_cluster()
        by_name: dict[str, _Person] = {}
        people: list[_Person] = []
        counts = {c.cluster_id: c.face_count for c in clusters}
        for cluster in clusters:
            name = cluster.name.strip()
            if name and name.casefold() in by_name:
                person = by_name[name.casefold()]
                person.cluster_ids.append(cluster.cluster_id)
                person.face_count += cluster.face_count
                if cluster.face_count > counts.get(person.rep_key, 0):
                    person.rep_key = cluster.cluster_id
            else:
                person = _Person(
                    name=name,
                    cluster_ids=[cluster.cluster_id],
                    face_count=cluster.face_count,
                    original_name=name,
                    rep_key=cluster.cluster_id,
                )
                if name:
                    by_name[name.casefold()] = person
                people.append(person)
        # Representative + hover faces per person, ranked by quality.
        for person in people:
            faces: list[dict] = []
            for cid in person.cluster_ids:
                faces.extend(faces_by_cluster.get(cid, []))
            ranked = rank_faces(faces)
            if ranked:
                person.rep_face = (ranked[0]["source"], ranked[0]["bbox"])
                person.extra_faces = [(f["source"], f["bbox"]) for f in ranked[1 : 1 + _MAX_HOVER_FACES]]
        people.sort(key=lambda p: p.face_count, reverse=True)
        return people

    def _reload(self) -> None:
        if self._connection is None:
            return
        self._people = self._build_people()
        self._update_stats()
        self._populate_cards()

    def _read_database_revision(self) -> tuple[object, ...]:
        if self._connection is None:
            return ()
        face_row = self._connection.execute(
            "SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM image_faces"
        ).fetchone()
        cluster_row = self._connection.execute(
            "SELECT COUNT(*), COALESCE(MAX(updated_at), '') FROM face_identity_clusters"
        ).fetchone()
        try:
            state_row = self._connection.execute(
                "SELECT COUNT(*), COALESCE(MAX(updated_at), '') FROM face_index_state"
            ).fetchone()
        except sqlite3.OperationalError:
            state_row = (0, "")
        return (*face_row, *cluster_row, *state_row)

    def _refresh_if_database_changed(self) -> None:
        if self._connection is None or self._active_cluster_task is not None:
            return
        if any(card._editing for card in self._cards):
            return
        revision = self._read_database_revision()
        if revision == self._database_revision:
            return
        self._database_revision = revision
        self._reconcile_people(self._build_people())

    def _reconcile_people(self, current: list[_Person]) -> None:
        """Update live scan results without rebuilding or reordering existing cards."""
        self._people = _merge_people_stably(self._people, current)
        self._update_stats()

        visible = self._visible_people()
        visible_keys = {person.rep_key for person in visible}
        changed_layout = False
        for card in list(self._cards):
            if card.person.rep_key not in visible_keys:
                self._cards.remove(card)
                self._card_by_key.pop(card.person.rep_key, None)
                self._grid.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
                changed_layout = True

        new_people: list[_Person] = []
        for person in visible:
            card = self._card_by_key.get(person.rep_key)
            if card is not None:
                card.person = person
                card._refresh_name()
                continue
            card = _PersonCard(person, self._grid_host)
            self._card_chrome(card)
            card.select_requested.connect(self._on_select_requested)
            card.name_committed.connect(self._on_name_committed)
            card.edit_started.connect(self._on_edit_started)
            card.hover_changed.connect(self._on_hover_changed)
            card.metrics_changed.connect(self._schedule_grid_host_sync)
            card.context_menu_requested.connect(self._show_person_menu)
            self._cards.append(card)
            self._card_by_key[person.rep_key] = card
            self._paint_cached_thumb(card)
            new_people.append(person)
            changed_layout = True

        has_cards = bool(self._cards)
        self.scroll.setVisible(has_cards)
        self.empty_label.setVisible(not has_cards)
        if not has_cards:
            self.empty_label.setText(self._empty_message())
        if changed_layout:
            self._relayout_grid()
            self._update_selection_ui()
            self._schedule_gutter_sync()
        if new_people:
            self._start_rep_crops(new_people)

    def _update_stats(self) -> None:
        named = sum(1 for p in self._people if p.named)
        unnamed = len(self._people) - named
        photos = sum(p.face_count for p in self._people)
        self.stats_label.setText(f"{named} Named / {unnamed} Unnamed / {photos} Total")

    def _visible_people(self) -> list[_Person]:
        show_singles = self.include_singles.isChecked()
        only_unnamed = self.filter_unnamed.isChecked()
        needle = self.search_edit.text().strip().casefold()
        result = []
        for person in self._people:
            # A "single-photo face" is a cluster with just one face - hidden by default.
            if not show_singles and person.face_count < 2:
                continue
            if only_unnamed and person.named:
                continue
            # Searching is a name lookup, so an unnamed person can never match.
            if needle and needle not in person.name.casefold():
                continue
            result.append(person)
        return result

    def _populate_cards(self) -> None:
        for card in self._cards:
            self._grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards = []
        self._card_by_key = {}
        self._focus_index = -1
        self._hover_cache.clear()  # rep_key routing is rebuilt below

        visible = self._visible_people()
        for person in visible:
            card = _PersonCard(person, self._grid_host)
            self._card_chrome(card)
            card.select_requested.connect(self._on_select_requested)
            card.name_committed.connect(self._on_name_committed)
            card.edit_started.connect(self._on_edit_started)
            card.hover_changed.connect(self._on_hover_changed)
            card.metrics_changed.connect(self._schedule_grid_host_sync)
            card.context_menu_requested.connect(self._show_person_menu)
            self._cards.append(card)
            self._card_by_key[person.rep_key] = card
            self._paint_cached_thumb(card)

        has_cards = bool(visible)
        self.scroll.setVisible(has_cards)
        self.empty_label.setVisible(not has_cards)
        if not has_cards:
            self.empty_label.setText(self._empty_message())

        self._relayout_grid()
        self._update_selection_ui()
        self._start_rep_crops(visible, restart=True)
        # Scrollbar visibility only settles after the layout pass; sync then.
        self._schedule_gutter_sync()

    def _column_count(self) -> int:
        # Base the count on the dialog width (reliable immediately after
        # resize()), not the viewport, whose size lags during layout.
        avail = max(_CARD_W, self.width() - 48 - 16)  # root margins + scrollbar
        return max(1, (avail + 18) // _TARGET_COL_W)

    def _relayout_grid(self) -> None:
        if not self._cards:
            return
        cols = self._column_count()
        self._current_cols = cols
        for card in self._cards:
            self._grid.removeWidget(card)
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // cols, index % cols)
        # Every column gets equal stretch so the cards divide the full width and
        # the outermost columns hug the left and right edges.
        max_cols = max(cols, self._grid.columnCount())
        for col in range(max_cols):
            self._grid.setColumnStretch(col, 1 if col < cols else 0)
        self._sync_grid_host_height()

    def _schedule_grid_host_sync(self) -> None:
        # Coalesce the many per-card signals one resize produces into one pass.
        QTimer.singleShot(0, self._sync_grid_host_height)

    def _sync_grid_host_height(self) -> None:
        """Make the grid host insist on the height its rows actually need.

        A resizable QScrollArea sizes its widget to the viewport unless the
        widget asks for more, and it does not re-ask when only the row count
        changes. Without this the grid quietly squeezes every card below its
        minimum height as soon as a filter or an undo brings more people back.
        """
        layout = self._grid_host.layout()
        layout.activate()
        self._grid_host.setMinimumHeight(layout.minimumSize().height())

    def _schedule_gutter_sync(self) -> None:
        # Defer one event-loop hop so the viewport width reflects the scrollbar
        # before we measure it (its geometry lags the range/resize signal).
        QTimer.singleShot(0, self._sync_scrollbar_gutter)

    def _sync_scrollbar_gutter(self) -> None:
        """Keep the cards the same distance from both window edges.

        The scrollbar eats into the viewport, so a plain equal margin leaves the
        grid sitting a scrollbar-width further from the right edge than the left.
        The scroll area is therefore allowed to reach into the right margin by
        exactly that width, and the header and filter rows take the width back as
        their own right inset so they stay flush with the cards.
        """
        # The reserved scrollbar space is exactly how much narrower the viewport
        # is than the scroll area itself (NoFrame, so no border to subtract).
        gutter = max(0, self.scroll.width() - self.scroll.viewport().width())
        left, top, _right, bottom = self._body_layout.getContentsMargins()
        self._body_layout.setContentsMargins(left, top, max(0, _BODY_MARGIN - gutter), bottom)
        for row in (self._header_row, self._filter_row):
            row_left, row_top, _row_right, row_bottom = row.getContentsMargins()
            row.setContentsMargins(row_left, row_top, gutter, row_bottom)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._column_count() != self._current_cols:
            self._relayout_grid()
        self._schedule_gutter_sync()

    def _empty_message(self) -> str:
        if self._connection is None:
            return ""
        identities = list_face_identities(self._connection)
        if not identities:
            return (
                "No faces indexed yet.\n\nFaces are found automatically in the background after a "
                "folder is indexed — check back shortly, or install the AI face model from AI Setup."
            )
        if self.filter_unnamed.isChecked() and self._people:
            return "Everyone shown is named.\n\nSwitch to “All” to review them."
        if self._people and not self.include_singles.isChecked():
            return "Only single-photo faces so far.\n\nTick “Include single-photo faces” to see them."
        return f"{len(identities)} face(s) found.\n\nClick “Rescan Faces” to group them into people."

    # -- crops -------------------------------------------------------------
    def _start_rep_crops(self, people: list[_Person], *, restart: bool = False) -> None:
        """Queue representative crops, painting anything already decoded at once.

        ``restart`` means the visible set was rebuilt (a filter, the singles
        switch, a search) and whatever is in flight is now for the wrong people:
        cancel it so this pass starts now. Without that, flipping the singles
        switch left a several-hundred-image pass running on the single crop
        thread and every new card sat blank until it drained.
        """
        if restart:
            if self._active_crop_task is not None:
                self._active_crop_task.cancel()
                self._active_crop_task = None
            self._pending_rep_people.clear()

        jobs = [
            (p.rep_key, 0, p.rep_face[0], p.rep_face[1])
            for p in people
            if p.rep_face and p.rep_key not in self._rep_cache
        ]
        if not jobs:
            return
        if self._active_crop_task is not None:
            self._pending_rep_people.update(
                (person.rep_key, person)
                for person in people
                if person.rep_face and person.rep_key not in self._rep_cache
            )
            return
        task = _CropTask(jobs, _THUMB_PX, self._crop_cache_dir)
        task.signals.loaded.connect(self._on_rep_crop, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(
            lambda finished=task: self._on_rep_crops_finished(finished),
            Qt.ConnectionType.QueuedConnection,
        )
        self._active_crop_task = task
        self._crop_pool.start(task)

    def _on_rep_crops_finished(self, task: _CropTask) -> None:
        # A cancelled pass still reports in; ignore it if a newer one took over.
        if self._active_crop_task is not task:
            return
        self._active_crop_task = None
        if self._pending_rep_people:
            pending = list(self._pending_rep_people.values())
            self._pending_rep_people.clear()
            self._start_rep_crops(pending)

    def _cache_rep(self, key: int, image: QImage) -> None:
        # Bounded so a very large library cannot grow this without limit.
        if len(self._rep_cache) >= _REP_CACHE_MAX:
            for oldest in list(self._rep_cache)[: len(self._rep_cache) - _REP_CACHE_MAX + 1]:
                self._rep_cache.pop(oldest, None)
        self._rep_cache[key] = image

    def _paint_cached_thumb(self, card: _PersonCard) -> None:
        cached = self._rep_cache.get(card.person.rep_key)
        if cached is not None:
            card.set_thumbnail(cached)

    def _on_rep_crop(self, key: int, _slot: int, image: QImage) -> None:
        self._cache_rep(key, image)
        card = self._card_by_key.get(key)
        if card is not None:
            card.set_thumbnail(image)

    # -- hover preview -----------------------------------------------------
    def _on_hover_changed(self, card: _PersonCard, entered: bool) -> None:
        if entered:
            self._hover_card = card
            if card.person.extra_faces:
                self._hover_timer.start()
        else:
            if self._hover_card is card:
                self._hover_card = None
            self._hover_timer.stop()
            self._hover_popover.hide()

    def _show_hover_preview(self) -> None:
        card = self._hover_card
        if card is None or not card.person.extra_faces:
            return
        faces = card.person.extra_faces[:_MAX_HOVER_FACES]
        key = card.person.rep_key
        self._hover_popover.show_for(len(faces))

        cached = self._hover_cache.get(key)
        complete = (
            cached is not None
            and len(cached) >= len(faces)
            and all(not img.isNull() for img in cached[: len(faces)])
        )
        if complete:
            # Already decoded once for this person — paint instantly, no task.
            for slot, image in enumerate(cached[: len(faces)]):
                self._hover_popover.set_face(slot, image)
        else:
            if self._active_hover_task is not None:
                self._active_hover_task.cancel()
            jobs = [(key, i, src, box) for i, (src, box) in enumerate(faces)]
            task = _CropTask(jobs, _HOVER_PX, self._crop_cache_dir)
            task.signals.loaded.connect(self._on_hover_crop, Qt.ConnectionType.QueuedConnection)
            self._active_hover_task = task
            self._hover_pool.start(task)  # dedicated pool: never blocked behind rep crops

        # Position above the card's thumbnail.
        self._hover_popover.adjustSize()
        top_left = card.thumb.mapToGlobal(QPoint(0, 0))
        px = top_left.x() + card.thumb.width() // 2 - self._hover_popover.sizeHint().width() // 2
        py = top_left.y() - self._hover_popover.sizeHint().height() - 8
        self._hover_popover.move(px, py)
        self._hover_popover.show()

    def _on_hover_crop(self, key: int, slot: int, image: QImage) -> None:
        bucket = self._hover_cache.setdefault(key, [])
        # Pad so slots can arrive out of order, then store for instant re-hover.
        while len(bucket) <= slot:
            bucket.append(QImage())
        bucket[slot] = image
        # Only paint if the user is still hovering the person this crop belongs to.
        if self._hover_card is not None and self._hover_card.person.rep_key == key:
            self._hover_popover.set_face(slot, image)

    # -- selection / merge -------------------------------------------------
    def _on_select_requested(self, card: _PersonCard, modifiers) -> None:
        additive = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        if not additive:
            was = card.is_selected()
            count = sum(1 for c in self._cards if c.is_selected())
            for other in self._cards:
                other.set_selected(False)
            card.set_selected(not (was and count == 1))
        else:
            card.set_selected(not card.is_selected())
        self._update_selection_ui()

    def _selected_cards(self) -> list[_PersonCard]:
        return [c for c in self._cards if c.is_selected()]

    def _clear_selection(self) -> None:
        for card in self._cards:
            card.set_selected(False)
        self._update_selection_ui()

    def _update_selection_ui(self) -> None:
        selected = self._selected_cards()
        count = len(selected)
        photos = sum(c.person.face_count for c in selected)
        self.action_row.setVisible(count > 0)
        # Merging needs two people to merge; ignoring and clearing need one.
        self.merge_button.setEnabled(count >= 2)
        self.merge_button.setToolTip(
            "" if count >= 2 else "Select two or more people to merge them"
        )
        self.ignore_button.setToolTip(
            f"Hide {count} selected · {photos} photos from this dialog and from people search"
            if count
            else ""
        )
        if count:
            self._hide_undo()

    # -- person menu -------------------------------------------------------
    def _show_person_menu(self, card: _PersonCard, position) -> None:
        self._focus_card_for_menu(card)
        self._build_person_menu(card).exec(position)

    def _focus_card_for_menu(self, card: _PersonCard) -> None:
        """Right-clicking an unselected card acts on that card alone.

        That is what every file manager does, and it keeps the menu's actions
        unambiguous when several people are already selected.
        """
        if card.is_selected():
            return
        self._clear_selection()
        card.set_selected(True)
        self._update_selection_ui()

    def _build_person_menu(self, card: _PersonCard) -> QMenu:
        person = card.person
        named = person.named
        selected = self._selected_cards()
        menu = QMenu(self)

        rename = menu.addAction("Rename" if named else "Name this person")
        rename.triggered.connect(card.begin_edit)

        clear = menu.addAction("Clear name")
        clear.setEnabled(named)
        clear.triggered.connect(lambda: self._on_name_committed(card, "", False))

        menu.addSeparator()

        target = _elide(person.name, 28) if named else "this person"
        show = menu.addAction(
            f"Show the only photo of {target}"
            if person.face_count == 1
            else f"Show all {person.face_count} photos of {target}"
        )
        show.triggered.connect(lambda: self._request_person_filter(card))

        if len(selected) >= 2:
            merge = menu.addAction(f"Merge {len(selected)} selected people")
            merge.triggered.connect(self._merge_selected)

        menu.addSeparator()
        word = "photo" if person.face_count == 1 else "photos"
        ignore = menu.addAction(f"Ignore this person ({person.face_count} {word})")
        ignore.triggered.connect(lambda: self._ignore_cards([card]))
        return menu

    def _person_photo_paths(self, person: _Person) -> list[str]:
        """Every source image this person's face was found in."""
        if self._connection is None or not person.cluster_ids:
            return []
        placeholders = ",".join("?" for _ in person.cluster_ids)
        rows = self._connection.execute(
            f"""
            SELECT DISTINCT images.source_path
            FROM image_faces
            JOIN images ON images.id = image_faces.image_id
            WHERE image_faces.cluster_id IN ({placeholders})
            """,
            tuple(int(cid) for cid in person.cluster_ids),
        ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def _request_person_filter(self, card: _PersonCard) -> None:
        """Hand the person's photos to the caller and close, so the grid filters."""
        person = card.person
        paths = self._person_photo_paths(person)
        if not paths:
            return
        word = "photo" if person.face_count == 1 else "photos"
        self.requested_person_label = (
            person.name.strip()
            if person.named
            else f"Unnamed face ({person.face_count} {word})"
        )
        self.requested_person_paths = tuple(paths)
        self.accept()

    # -- search ------------------------------------------------------------
    def _on_search_changed(self, _text: str) -> None:
        self._search_timer.start()

    # -- ignore ------------------------------------------------------------
    def _ignore_selected(self) -> None:
        self._ignore_cards(self._selected_cards())

    def _ignore_cards(self, cards: list[_PersonCard]) -> None:
        if self._connection is None:
            return
        selected = list(cards)
        if not selected:
            return
        cluster_ids = [cid for card in selected for cid in card.person.cluster_ids]
        set_clusters_ignored(self._connection, cluster_ids, True)
        self._connection.commit()
        self._ignored_undo = cluster_ids
        self._clear_selection()
        self._reload()
        self._database_revision = self._read_database_revision()
        people = "person" if len(selected) == 1 else "people"
        self.undo_label.setText(f"{len(selected)} {people} ignored")
        self.undo_row.setVisible(True)

    def _undo_ignore(self) -> None:
        if self._connection is None or not self._ignored_undo:
            return
        set_clusters_ignored(self._connection, self._ignored_undo, False)
        self._connection.commit()
        self._ignored_undo = []
        self._hide_undo()
        self._reload()
        self._database_revision = self._read_database_revision()

    def _hide_undo(self) -> None:
        self.undo_row.setVisible(False)

    def _merge_selected(self) -> None:
        if self._connection is None:
            return
        selected = self._selected_cards()
        if len(selected) < 2:
            return
        suggested = next((c.person.name for c in selected if c.person.named), "")
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self,
            "Merge as Same Person",
            f"Name for this person ({sum(c.person.face_count for c in selected)} photos):",
            QLineEdit.EchoMode.Normal,
            suggested,
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        for card in selected:
            for cid in card.person.cluster_ids:
                assign_person_name(self._connection, int(cid), name)
        self._connection.commit()
        self._reload()

    # -- naming (immediate save, no reorder) -------------------------------
    def _on_edit_started(self, card: _PersonCard) -> None:
        self._clear_selection()

    def _on_name_committed(self, card: _PersonCard, text: str, via_enter: bool) -> None:
        if self._connection is not None and text != card.person.original_name:
            task = _NameSaveTask(
                str(self._db_path),
                card.person.cluster_ids,
                text,
                card.person.original_name,
                card.person.rep_key,
            )
            task.signals.finished.connect(self._on_name_save_finished, Qt.ConnectionType.QueuedConnection)
            task.signals.failed.connect(self._on_name_save_failed, Qt.ConnectionType.QueuedConnection)
            self._name_save_tasks.add(task)
            card.apply_name(text)
            self._update_stats()  # positions unchanged (#8): do NOT repopulate
            _name_write_pool().start(task)
        if via_enter:
            self._focus_next_unnamed(after=card)

    def _on_name_save_finished(self, task: _NameSaveTask) -> None:
        self._name_save_tasks.discard(task)

    def _on_name_save_failed(self, task: _NameSaveTask, message: str) -> None:
        self._name_save_tasks.discard(task)
        card = self._card_by_key.get(task.rep_key)
        if card is not None and card.person.original_name == task.name:
            card.apply_name(task.previous_name)
            self._update_stats()
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "Tag People", f"Could not save the name.\n\n{message}")

    def _focus_next_unnamed(self, *, after: _PersonCard) -> None:
        try:
            start = self._cards.index(after)
        except ValueError:
            start = -1
        for card in self._cards[start + 1 :]:
            if not card.person.named:
                card.begin_edit()
                return

    def _rescan(self) -> None:
        if self._connection is None or self._active_cluster_task is not None:
            return
        from ..ai_model import active_face_identity_model

        self.scan_button.setEnabled(False)
        self.scan_button.setText("Scanning…")

        progress = QProgressDialog("Grouping faces into people…", None, 0, 0, self)
        progress.setWindowTitle("Rescan Faces")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        self._scan_progress = progress

        task = _ClusterTask(str(self._db_path), active_face_identity_model())
        task.signals.finished.connect(self._on_rescan_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._on_rescan_failed, Qt.ConnectionType.QueuedConnection)
        self._active_cluster_task = task
        progress.show()
        QThreadPool.globalInstance().start(task)

    def _finish_rescan(self) -> None:
        self._active_cluster_task = None
        if self._scan_progress is not None:
            self._scan_progress.close()
            self._scan_progress = None
        self.scan_button.setText("Rescan Faces")
        self.scan_button.setEnabled(True)

    def _on_rescan_finished(self) -> None:
        self._finish_rescan()
        self._reload()
        self._database_revision = self._read_database_revision()

    def _on_rescan_failed(self, message: str) -> None:
        self._finish_rescan()
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "Rescan Faces", f"Could not group faces.\n\n{message}")

    # -- keyboard navigation ----------------------------------------------
    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if not self._cards or any(c._editing for c in self._cards):
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._move_focus(key)
            return
        if key == Qt.Key.Key_Space and 0 <= self._focus_index < len(self._cards):
            card = self._cards[self._focus_index]
            self._on_select_requested(card, Qt.KeyboardModifier.ControlModifier)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and 0 <= self._focus_index < len(self._cards):
            self._cards[self._focus_index].begin_edit()
            return
        if key == Qt.Key.Key_Escape and self._selected_cards():
            self._clear_selection()
            return
        super().keyPressEvent(event)

    def _move_focus(self, key) -> None:
        cols = max(1, self._current_cols)
        if self._focus_index < 0:
            new = 0
        else:
            new = self._focus_index
            if key == Qt.Key.Key_Left:
                new -= 1
            elif key == Qt.Key.Key_Right:
                new += 1
            elif key == Qt.Key.Key_Up:
                new -= cols
            elif key == Qt.Key.Key_Down:
                new += cols
        new = max(0, min(len(self._cards) - 1, new))
        if 0 <= self._focus_index < len(self._cards):
            self._cards[self._focus_index].set_keyboard_focus(False)
        self._focus_index = new
        card = self._cards[new]
        card.set_keyboard_focus(True)
        self.scroll.ensureWidgetVisible(card)
