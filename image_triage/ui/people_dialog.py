from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
)
from ..quality.store import ensure_faces_table
from aiculler.storage import SQLITE_BUSY_TIMEOUT_MS

# A "single-photo face" is a cluster with just one face — hidden by default.
_SINGLE_PHOTO = 1
_THUMB_PX = 128
_HOVER_PX = 84
_CARD_W = 196
_TARGET_COL_W = _CARD_W + 18  # card + grid gap, for responsive column count
_MAX_HOVER_FACES = 4
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


class _PersonCard(QFrame):
    select_requested = Signal(object, object)  # card, modifiers
    name_committed = Signal(object, str, bool)  # card, text, via_enter
    edit_started = Signal(object)
    hover_changed = Signal(object, bool)  # card, entered

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
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)

        self.thumb = QLabel(self)
        self.thumb.setFixedSize(_THUMB_PX, _THUMB_PX)
        self.thumb.setObjectName("personThumb")
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setText(person.name.strip()[:1].upper() if person.named else "")
        layout.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignHCenter)

        # Name control: a flat button that swaps to an inline edit on click.
        self._name_stack = QStackedLayout()
        self._name_stack.setContentsMargins(0, 0, 0, 0)
        self.name_button = QPushButton(self)
        self.name_button.setObjectName("nameButton")
        self.name_button.setFlat(True)
        self.name_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name_button.clicked.connect(self.begin_edit)
        self.name_edit = _NameEdit(self)
        self.name_edit.setObjectName("nameEdit")
        self.name_edit.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.name_edit.submitted.connect(lambda: self._commit(via_enter=True))
        self.name_edit.editingFinished.connect(lambda: self._commit(via_enter=False))
        self.name_edit.escaped.connect(self._cancel_edit)
        name_host = QWidget(self)
        self._name_stack.addWidget(self.name_button)
        self._name_stack.addWidget(self.name_edit)
        name_host.setLayout(self._name_stack)
        layout.addWidget(name_host)

        self.count_label = QLabel(self)
        self.count_label.setObjectName("personCount")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.count_label)

        self._editing = False
        self._refresh_name()

    # -- name control ------------------------------------------------------
    def _refresh_name(self) -> None:
        if self.person.named:
            self.name_button.setText(self.person.name)
            self.name_button.setProperty("state", "named")
        else:
            self.name_button.setText("+ Add name")
            self.name_button.setProperty("state", "unnamed")
        self.name_button.style().unpolish(self.name_button)
        self.name_button.style().polish(self.name_button)
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
        self._name_stack.setCurrentWidget(self.name_edit)
        self.name_edit.setFocus()
        self.name_edit.selectAll()
        self.edit_started.emit(self)

    def _commit(self, *, via_enter: bool) -> None:
        if not self._editing:
            return
        self._editing = False
        text = self.name_edit.text().strip()
        self._name_stack.setCurrentWidget(self.name_button)
        if text != self.person.original_name:
            self.name_committed.emit(self, text, via_enter)
        elif via_enter:
            self.name_committed.emit(self, text, True)  # allow advance even with no change

    def _cancel_edit(self) -> None:
        self._editing = False
        self._name_stack.setCurrentWidget(self.name_button)
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
        self.thumb.setText("")
        self.thumb.setPixmap(_circular_pixmap(image, _THUMB_PX))

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
        # Sized so the default 3-column grid shows three full rows without
        # scrolling (card ~207px high + 18px gaps + header/filter/footer chrome).
        self.resize(732, 840)
        self.setMinimumSize(560, 480)
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

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)

        # -- header: title + stats + scan control --------------------------
        header = QHBoxLayout()
        header.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self.title_label = QLabel("Tag People", self)
        self.title_label.setObjectName("peopleTitle")
        title_col.addWidget(self.title_label)
        self.stats_label = QLabel("", self)
        self.stats_label.setObjectName("peopleStats")
        title_col.addWidget(self.stats_label)
        self.progress = QProgressBar(self)
        self.progress.setObjectName("scanProgress")
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.setVisible(False)
        title_col.addWidget(self.progress)
        header.addLayout(title_col, 1)
        self.scan_button = QPushButton("Rescan Faces", self)
        self.scan_button.setObjectName("scanButton")
        self.scan_button.clicked.connect(self._rescan)
        header.addWidget(self.scan_button, 0, Qt.AlignmentFlag.AlignTop)
        self._header_row = header
        root.addLayout(header)

        # -- filter row ----------------------------------------------------
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.filter_group = QButtonGroup(self)
        self.filter_all = QPushButton("All", self)
        self.filter_unnamed = QPushButton("Unnamed", self)
        for i, btn in enumerate((self.filter_all, self.filter_unnamed)):
            btn.setObjectName("segButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.filter_group.addButton(btn, i)
            filters.addWidget(btn)
        self.filter_all.setChecked(True)
        self.filter_group.idClicked.connect(lambda _id: self._reload())
        filters.addStretch(1)
        self.include_singles = QCheckBox("Include single-photo faces", self)
        self.include_singles.toggled.connect(self._reload)
        filters.addWidget(self.include_singles)
        self._filter_row = filters
        root.addLayout(filters)

        # -- grid (responsive, centred) -----------------------------------
        self.scroll = QScrollArea(self)
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
        # right edges line up with the header, filter and footer rows.
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(18)
        outer.addLayout(self._grid)
        outer.addStretch(1)
        self.scroll.setWidget(self._grid_host)
        root.addWidget(self.scroll, 1)

        self.empty_label = QLabel("", self)
        self.empty_label.setObjectName("peopleEmpty")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setVisible(False)
        root.addWidget(self.empty_label, 1)

        # -- contextual selection bar (only when >=1 selected) -------------
        self.selection_bar = QFrame(self)
        self.selection_bar.setObjectName("selectionBar")
        self.selection_bar.setVisible(False)
        sel = QHBoxLayout(self.selection_bar)
        sel.setContentsMargins(14, 10, 14, 10)
        sel.setSpacing(12)
        self.selection_label = QLabel("", self.selection_bar)
        self.selection_label.setObjectName("selectionLabel")
        sel.addWidget(self.selection_label)
        sel.addStretch(1)
        self.clear_button = QPushButton("Clear", self.selection_bar)
        self.clear_button.clicked.connect(self._clear_selection)
        sel.addWidget(self.clear_button)
        self.merge_button = QPushButton("Merge as Same Person", self.selection_bar)
        self.merge_button.setObjectName("mergeButton")
        self.merge_button.clicked.connect(self._merge_selected)
        sel.addWidget(self.merge_button)
        root.addWidget(self.selection_bar)

        # -- footer --------------------------------------------------------
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.done_button = QPushButton("Done", self)
        self.done_button.setObjectName("doneButton")
        self.done_button.setDefault(False)
        self.done_button.setAutoDefault(False)
        self.done_button.clicked.connect(self.accept)
        footer.addWidget(self.done_button)
        self._footer_row = footer
        root.addLayout(footer)

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
        self._reload()
        self._database_revision = self._read_database_revision()
        self._refresh_timer.start()

    # -- styling -----------------------------------------------------------
    def _stylesheet(self) -> str:
        pal = self.palette()
        base = pal.color(QPalette.ColorRole.Base)
        text = pal.color(QPalette.ColorRole.Text)
        mid = pal.color(QPalette.ColorRole.Mid)
        hl = pal.color(QPalette.ColorRole.Highlight)
        on_hl = pal.color(QPalette.ColorRole.HighlightedText).name()
        muted = pal.color(QPalette.ColorRole.PlaceholderText).name()
        subtle = _blend(base, text, 0.10).name()  # faint normal border
        tint = QColor(hl.red(), hl.green(), hl.blue(), 28).name(QColor.NameFormat.HexArgb)
        accent = hl.name()
        radius = _THUMB_PX // 2
        return f"""
            QLabel#peopleTitle {{ color: {text.name()}; font-size: 17px; font-weight: 600; }}
            QLabel#peopleStats {{ color: {muted}; font-size: 12px; }}
            QLabel#peopleEmpty {{ color: {muted}; font-size: 13px; }}
            QProgressBar#scanProgress {{ background: {mid.name()}; border: none; border-radius: 1px; }}
            QProgressBar#scanProgress::chunk {{ background: {accent}; border-radius: 1px; }}

            QPushButton#segButton {{
                padding: 5px 14px; border-radius: 7px; border: 1px solid {subtle};
                background: transparent; color: {muted}; font-size: 12px;
            }}
            QPushButton#segButton:hover {{ border-color: {accent}; }}
            QPushButton#segButton:checked {{ color: {text.name()}; border-color: {accent}; background: {tint}; }}

            QFrame#personCard {{ background: {base.name()}; border: 1px solid {subtle}; border-radius: 14px; }}
            QFrame#personCard:hover {{ border-color: {_blend(base, hl, 0.5).name()}; }}
            QFrame#personCard[focused="true"] {{ border: 1px dashed {accent}; }}
            QFrame#personCard[selected="true"] {{ border: 2px solid {accent}; background: {tint}; }}
            QLabel#personThumb {{ background: {mid.name()}; border-radius: {radius}px; color: {muted};
                font-size: 30px; font-weight: 600; }}
            QPushButton#nameButton {{ border: none; background: transparent; padding: 2px 4px; font-size: 15px; }}
            QPushButton#nameButton[state="named"] {{ color: {text.name()}; font-weight: 600; }}
            QPushButton#nameButton[state="unnamed"] {{ color: {accent}; font-weight: 600; }}
            QLineEdit#nameEdit {{ border: none; border-bottom: 1px solid {accent}; background: transparent;
                color: {text.name()}; font-size: 15px; padding: 2px; }}
            QLabel#personCount {{ color: {muted}; font-size: 12px; }}

            QFrame#selectionBar {{ background: {tint}; border: 1px solid {accent}; border-radius: 10px; }}
            QLabel#selectionLabel {{ color: {text.name()}; font-size: 13px; font-weight: 500; }}
            QFrame#hoverPreview {{ background: {base.name()}; border: 1px solid {accent}; border-radius: 12px; }}

            QPushButton {{ padding: 8px 18px; min-width: 92px; border-radius: 8px;
                border: 1px solid {mid.name()}; background: transparent; color: {text.name()}; font-size: 13px; }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton#doneButton, QPushButton#mergeButton {{
                background: {accent}; color: {on_hl}; border: none; font-weight: 600; }}
            QPushButton#scanButton {{ padding: 7px 14px; }}
        """

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
            card.select_requested.connect(self._on_select_requested)
            card.name_committed.connect(self._on_name_committed)
            card.edit_started.connect(self._on_edit_started)
            card.hover_changed.connect(self._on_hover_changed)
            self._cards.append(card)
            self._card_by_key[person.rep_key] = card
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
        self.stats_label.setText(f"{named} named · {unnamed} unnamed · {photos} photos")

    def _visible_people(self) -> list[_Person]:
        show_singles = self.include_singles.isChecked()
        only_unnamed = self.filter_unnamed.isChecked()
        result = []
        for person in self._people:
            if not show_singles and person.face_count < 2:
                continue
            if only_unnamed and person.named:
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
            card.select_requested.connect(self._on_select_requested)
            card.name_committed.connect(self._on_name_committed)
            card.edit_started.connect(self._on_edit_started)
            card.hover_changed.connect(self._on_hover_changed)
            self._cards.append(card)
            self._card_by_key[person.rep_key] = card

        has_cards = bool(visible)
        self.scroll.setVisible(has_cards)
        self.empty_label.setVisible(not has_cards)
        if not has_cards:
            self.empty_label.setText(self._empty_message())

        self._relayout_grid()
        self._update_selection_ui()
        self._start_rep_crops(visible)
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

    def _schedule_gutter_sync(self) -> None:
        # Defer one event-loop hop so the viewport width reflects the scrollbar
        # before we measure it (its geometry lags the range/resize signal).
        QTimer.singleShot(0, self._sync_scrollbar_gutter)

    def _sync_scrollbar_gutter(self) -> None:
        """Reserve the same right-hand gutter on the header/filter/footer rows as
        the scroll area's vertical scrollbar takes, so the grid's right edge lines
        up with them whether or not the scrollbar is showing."""
        # The reserved scrollbar space is exactly how much narrower the viewport
        # is than the scroll area itself (NoFrame, so no border to subtract).
        gutter = max(0, self.scroll.width() - self.scroll.viewport().width())
        for row in (self._header_row, self._filter_row, self._footer_row):
            left, top, _right, bottom = row.getContentsMargins()
            row.setContentsMargins(left, top, gutter, bottom)

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
    def _start_rep_crops(self, people: list[_Person]) -> None:
        jobs = [(p.rep_key, 0, p.rep_face[0], p.rep_face[1]) for p in people if p.rep_face]
        if not jobs:
            return
        if self._active_crop_task is not None:
            self._pending_rep_people.update((person.rep_key, person) for person in people if person.rep_face)
            return
        task = _CropTask(jobs, _THUMB_PX, self._crop_cache_dir)
        task.signals.loaded.connect(self._on_rep_crop, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._on_rep_crops_finished, Qt.ConnectionType.QueuedConnection)
        self._active_crop_task = task
        self._crop_pool.start(task)

    def _on_rep_crops_finished(self) -> None:
        self._active_crop_task = None
        if self._pending_rep_people:
            pending = list(self._pending_rep_people.values())
            self._pending_rep_people.clear()
            self._start_rep_crops(pending)

    def _on_rep_crop(self, key: int, _slot: int, image: QImage) -> None:
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
        self.selection_bar.setVisible(count > 0)
        self.selection_label.setText(f"{count} selected · {photos} photos" if count else "")
        self.merge_button.setVisible(count >= 2)

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
