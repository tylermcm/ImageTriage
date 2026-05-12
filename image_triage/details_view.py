from __future__ import annotations

from datetime import datetime
import re
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QKeyEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .formats import suffix_for_path
from .models import ImageRecord, SessionAnnotation
from .scanner import normalized_path_key
from .thumbnails import ThumbnailManager


def _format_bytes(size: int) -> str:
    value = float(max(0, int(size)))
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "bytes":
                return f"{int(value)} bytes"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def _format_modified(ns: int) -> str:
    if ns <= 0:
        return "-"
    try:
        return datetime.fromtimestamp(ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "-"


def _file_type(record: ImageRecord) -> str:
    if record.is_folder:
        return "Folder"
    suffix = suffix_for_path(record.path).lstrip(".").upper()
    return f"{suffix} File" if suffix else "File"


def _decision_text(annotation: SessionAnnotation | None) -> str:
    if annotation is None:
        return "Unreviewed"
    if annotation.reject:
        return "Reject"
    if annotation.winner:
        return "Keep"
    return "Unreviewed"


def _rating_text(annotation: SessionAnnotation | None) -> str:
    if annotation is None or annotation.rating <= 0:
        return "-"
    return str(annotation.rating)


def _natural_name_key(value: str) -> tuple[tuple[int, object], ...]:
    parts = re.split(r"(\d+)", value.casefold())
    key: list[tuple[int, object]] = []
    for part in parts:
        if not part:
            continue
        key.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(key)


class DetailsTableModel(QAbstractTableModel):
    COLUMNS = ("Name", "Decision", "Rating", "AI", "Date modified", "Type", "Size")

    def __init__(self, *, ai_text_provider: Callable[[ImageRecord], str], parent=None) -> None:
        super().__init__(parent)
        self._records: list[ImageRecord] = []
        self._rows: list[int] = []
        self._annotations: dict[str, SessionAnnotation] = {}
        self._ai_text_provider = ai_text_provider
        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder

    def set_records(self, records: list[ImageRecord]) -> None:
        self.beginResetModel()
        self._records = list(records)
        self._rows = list(range(len(self._records)))
        self._sort_rows()
        self.endResetModel()

    def append_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        self.beginResetModel()
        self._records.extend(records)
        self._rows = list(range(len(self._records)))
        self._sort_rows()
        self.endResetModel()

    def set_annotations(self, annotations: dict[str, SessionAnnotation]) -> None:
        self._annotations = annotations
        if self._sort_column in {1, 2}:
            self.layoutAboutToBeChanged.emit()
            self._sort_rows()
            self.layoutChanged.emit()
        self.refresh_rows()

    def refresh_rows(self, rows: set[int] | None = None) -> None:
        if not self._records:
            return
        if rows:
            for source_row in rows:
                row = self.row_for_source_index(source_row)
                if row is not None:
                    self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.COLUMNS) - 1))
            return
        self.dataChanged.emit(self.index(0, 0), self.index(len(self._records) - 1, len(self.COLUMNS) - 1))

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return section + 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        source_row = self._rows[index.row()]
        record = self._records[source_row]
        annotation = self._annotations.get(record.path)
        column = index.column()
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in {2, 6}:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == 0:
            return record.name
        if column == 1:
            return _decision_text(annotation)
        if column == 2:
            return _rating_text(annotation)
        if column == 3:
            return "-" if record.is_folder else self._ai_text_provider(record)
        if column == 4:
            return _format_modified(record.modified_ns)
        if column == 5:
            return _file_type(record)
        if column == 6:
            return "" if record.is_folder else _format_bytes(record.size)
        return None

    def record_at(self, row: int) -> ImageRecord | None:
        source_row = self.source_index_at(row)
        if source_row is None:
            return None
        return self._records[source_row]

    def record_for_source_index(self, source_index: int) -> ImageRecord | None:
        if not 0 <= source_index < len(self._records):
            return None
        return self._records[source_index]

    def source_index_at(self, row: int) -> int | None:
        if not 0 <= row < len(self._rows):
            return None
        return self._rows[row]

    def row_for_source_index(self, source_index: int) -> int | None:
        if not 0 <= source_index < len(self._records):
            return None
        try:
            return self._rows.index(source_index)
        except ValueError:
            return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        if not 0 <= column < len(self.COLUMNS):
            return
        self.layoutAboutToBeChanged.emit()
        self._sort_column = column
        self._sort_order = order
        self._sort_rows()
        self.layoutChanged.emit()

    def _sort_rows(self) -> None:
        reverse = self._sort_order == Qt.SortOrder.DescendingOrder

        def sort_value(source_row: int):
            record = self._records[source_row]
            annotation = self._annotations.get(record.path)
            if self._sort_column == 1:
                return _decision_text(annotation).casefold()
            if self._sort_column == 2:
                return annotation.rating if annotation is not None else 0
            if self._sort_column == 3:
                return "" if record.is_folder else self._ai_text_provider(record).casefold()
            if self._sort_column == 4:
                return record.modified_ns
            if self._sort_column == 5:
                return _file_type(record).casefold()
            if self._sort_column == 6:
                return record.size
            return _natural_name_key(record.name)

        folders = [row for row in self._rows if self._records[row].is_folder]
        files = [row for row in self._rows if not self._records[row].is_folder]
        folders.sort(key=lambda row: _natural_name_key(self._records[row].name), reverse=reverse)
        files.sort(key=sort_value, reverse=reverse)
        self._rows = folders + files


class DetailsTableView(QTableView):
    preview_requested = Signal(int)
    delete_requested = Signal(int)
    keep_requested = Signal(int)
    move_requested = Signal(int)
    rate_requested = Signal(int, int)
    tag_requested = Signal(int)
    winner_requested = Signal(int)
    reject_requested = Signal(int)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        row = self.currentIndex().row()
        if row < 0:
            super().keyPressEvent(event)
            return
        model = self.model()
        record = model.record_at(row) if isinstance(model, DetailsTableModel) else None
        key = event.key()
        modifiers = event.modifiers()
        review_allowed = not bool(
            modifiers
            & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
        )
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and review_allowed:
            self.preview_requested.emit(row)
            return
        if record is None or record.is_folder:
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Delete and review_allowed:
            self.delete_requested.emit(row)
            return
        if key == Qt.Key.Key_K and review_allowed:
            self.keep_requested.emit(row)
            return
        if key == Qt.Key.Key_M and review_allowed:
            self.move_requested.emit(row)
            return
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_5 and review_allowed:
            self.rate_requested.emit(row, key - Qt.Key.Key_0)
            return
        if key == Qt.Key.Key_T and review_allowed:
            self.tag_requested.emit(row)
            return
        if key == Qt.Key.Key_W and review_allowed:
            self.winner_requested.emit(row)
            return
        if key == Qt.Key.Key_X and review_allowed:
            self.reject_requested.emit(row)
            return
        super().keyPressEvent(event)


class DetailsHeaderView(QHeaderView):
    def __init__(self, orientation: Qt.Orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self._pressed_section = -1
        self._hover_section = -1
        self.setSectionsClickable(True)
        self.setSortIndicatorShown(False)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(38)
        self.setMouseTracking(True)

    def paintSection(self, painter: QPainter, rect, logicalIndex: int) -> None:
        sort_section = self.sortIndicatorSection()
        sort_order = self.sortIndicatorOrder()
        self.setSortIndicatorShown(False)
        super().paintSection(painter, rect, logicalIndex)
        self.setSortIndicatorShown(True)
        if logicalIndex == self._pressed_section or logicalIndex == self._hover_section:
            painter.save()
            color = self.palette().highlight().color()
            color.setAlpha(58 if logicalIndex == self._pressed_section else 28)
            painter.fillRect(rect.adjusted(1, 1, -1, -1), color)
            painter.restore()
        if logicalIndex != sort_section:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self.palette().color(self.foregroundRole()), 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        center_x = rect.center().x()
        y = rect.top() + 8
        path = QPainterPath()
        if sort_order == Qt.SortOrder.AscendingOrder:
            path.moveTo(QPointF(center_x - 3.0, y + 2.0))
            path.lineTo(QPointF(center_x, y - 1.0))
            path.lineTo(QPointF(center_x + 3.0, y + 2.0))
        else:
            path.moveTo(QPointF(center_x - 3.0, y - 1.0))
            path.lineTo(QPointF(center_x, y + 2.0))
            path.lineTo(QPointF(center_x + 3.0, y - 1.0))
        painter.drawPath(path)
        painter.restore()

    def mousePressEvent(self, event) -> None:
        self._pressed_section = self.logicalIndexAt(event.position().toPoint())
        self.viewport().update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._pressed_section = -1
        self.viewport().update()
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        section = self.logicalIndexAt(event.position().toPoint())
        if section != self._hover_section:
            self._hover_section = section
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_section = -1
        self._pressed_section = -1
        self.viewport().update()
        super().leaveEvent(event)


class DetailsPreviewPane(QWidget):
    def __init__(self, thumbnail_manager: ThumbnailManager, parent=None) -> None:
        super().__init__(parent)
        self._thumbnail_manager = thumbnail_manager
        self._record: ImageRecord | None = None
        self._image = QImage()
        self._image_key_path = ""
        self.setObjectName("detailsPreviewPane")
        self.setMinimumWidth(260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.preview_label = QLabel("No image selected")
        self.preview_label.setObjectName("detailsPreviewImage")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(220, 180)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview_label.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.preview_label, 1)

        self._thumbnail_manager.thumbnail_ready.connect(self._handle_thumbnail_ready)

    def set_record(self, record: ImageRecord | None) -> None:
        self._record = record
        self._image = QImage()
        self._image_key_path = ""
        if record is None:
            self.preview_label.setText("No image selected")
            self.preview_label.setPixmap(QPixmap())
            return
        if record.is_folder:
            self.preview_label.setText("Folder")
            self.preview_label.setPixmap(QPixmap())
            return
        variant = record.display_variants[0]
        target = self._target_size()
        cached = self._thumbnail_manager.get_cached(variant, target)
        if cached is not None and not cached.isNull():
            self._set_image(cached)
            return
        key = self._thumbnail_manager.request_thumbnail(variant, target, priority=25_000)
        self._image_key_path = normalized_path_key(key.path)
        self.preview_label.setText("Loading preview...")
        self.preview_label.setPixmap(QPixmap())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_image()

    def _target_size(self) -> QSize:
        size = self.preview_label.size()
        return QSize(max(240, size.width()), max(180, size.height()))

    def _handle_thumbnail_ready(self, key, image) -> None:
        if self._record is None or self._record.is_folder:
            return
        if normalized_path_key(getattr(key, "path", "")) != self._image_key_path:
            return
        if image is None or image.isNull():
            return
        self._set_image(image)

    def _set_image(self, image: QImage) -> None:
        self._image = image
        self._render_image()

    def _render_image(self) -> None:
        if self._image.isNull():
            return
        pixmap = QPixmap.fromImage(self._image)
        if pixmap.isNull():
            return
        self.preview_label.setText("")
        self.preview_label.setPixmap(
            pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class PhotoDetailsView(QWidget):
    current_changed = Signal(int)
    selection_changed = Signal()
    preview_requested = Signal(int)
    context_menu_requested = Signal(int, object)
    delete_requested = Signal(int)
    keep_requested = Signal(int)
    move_requested = Signal(int)
    rate_requested = Signal(int, int)
    tag_requested = Signal(int)
    winner_requested = Signal(int)
    reject_requested = Signal(int)

    def __init__(
        self,
        thumbnail_manager: ThumbnailManager,
        *,
        ai_text_provider: Callable[[ImageRecord], str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._syncing = False
        self._pending_preview_row = -1
        self._preview_update_timer = QTimer(self)
        self._preview_update_timer.setSingleShot(True)
        self._preview_update_timer.setInterval(80)
        self._preview_update_timer.timeout.connect(self._apply_pending_preview_row)
        self._model = DetailsTableModel(ai_text_provider=ai_text_provider, parent=self)

        self.table = DetailsTableView(self)
        self.table.setObjectName("detailsTableView")
        self.table.setModel(self._model)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        header = DetailsHeaderView(Qt.Orientation.Horizontal, self.table)
        self.table.setHorizontalHeader(header)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(DetailsTableModel.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda index: self._emit_row_signal(self.preview_requested, index.row()) if index.isValid() else None)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        selection_model = self.table.selectionModel()
        selection_model.currentRowChanged.connect(self._handle_current_row_changed)
        selection_model.selectionChanged.connect(self._handle_selection_changed)

        self.table.preview_requested.connect(lambda row: self._emit_row_signal(self.preview_requested, row))
        self.table.delete_requested.connect(lambda row: self._emit_row_signal(self.delete_requested, row))
        self.table.keep_requested.connect(lambda row: self._emit_row_signal(self.keep_requested, row))
        self.table.move_requested.connect(lambda row: self._emit_row_signal(self.move_requested, row))
        self.table.rate_requested.connect(lambda row, rating: self._emit_rate_signal(row, rating))
        self.table.tag_requested.connect(lambda row: self._emit_row_signal(self.tag_requested, row))
        self.table.winner_requested.connect(lambda row: self._emit_row_signal(self.winner_requested, row))
        self.table.reject_requested.connect(lambda row: self._emit_row_signal(self.reject_requested, row))

        self.preview_pane = DetailsPreviewPane(thumbnail_manager, self)
        self.preview_toggle = QCheckBox("Preview pane")
        self.preview_toggle.setObjectName("detailsPreviewToggle")
        self.preview_toggle.setChecked(True)
        self.preview_toggle.toggled.connect(self.set_preview_visible)

        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 1, 10, 1)
        header_layout.setSpacing(0)
        header_layout.addStretch(1)
        header_layout.addWidget(self.preview_toggle)
        header.setMaximumHeight(26)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.preview_pane)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([760, 300])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.splitter, 1)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    def set_records(self, records: list[ImageRecord]) -> None:
        self._model.set_records(records)
        self._queue_preview_row(self.table.currentIndex().row(), immediate=True)

    def append_records(self, records: list[ImageRecord]) -> None:
        self._model.append_records(records)

    def set_annotations(self, annotations: dict[str, SessionAnnotation]) -> None:
        self._model.set_annotations(annotations)

    def refresh_rows(self, rows: set[int] | None = None) -> None:
        self._model.refresh_rows(rows)

    def set_preview_visible(self, visible: bool) -> None:
        self.preview_pane.setVisible(bool(visible))
        if self.preview_toggle.isChecked() != bool(visible):
            self.preview_toggle.setChecked(bool(visible))
        if visible:
            self._queue_preview_row(self.table.currentIndex().row(), immediate=True)

    def selected_indexes(self) -> list[int]:
        rows = {index.row() for index in self.table.selectionModel().selectedRows() if index.isValid()}
        source_rows = [self._model.source_index_at(row) for row in rows]
        return sorted(row for row in source_rows if row is not None)

    def current_index(self) -> int:
        source_row = self._model.source_index_at(self.table.currentIndex().row())
        return source_row if source_row is not None else -1

    def current_record(self) -> ImageRecord | None:
        return self._model.record_for_source_index(self.current_index())

    def _emit_row_signal(self, signal: Signal, row: int) -> None:
        source_row = self._model.source_index_at(row)
        if source_row is not None:
            signal.emit(source_row)

    def _emit_rate_signal(self, row: int, rating: int) -> None:
        source_row = self._model.source_index_at(row)
        if source_row is not None:
            self.rate_requested.emit(source_row, rating)

    def set_current_index(self, index: int) -> None:
        row = self._model.row_for_source_index(index)
        if row is None:
            self.preview_pane.set_record(None)
            return
        self._syncing = True
        try:
            model_index = self._model.index(row, 0)
            self.table.setCurrentIndex(model_index)
            self.table.scrollTo(model_index, QAbstractItemView.ScrollHint.EnsureVisible)
            self._queue_preview_row(row)
        finally:
            self._syncing = False

    def set_selected_indexes(self, indexes: list[int], *, current_index: int | None = None) -> None:
        valid = sorted({index for index in indexes if self._model.row_for_source_index(index) is not None})
        selection_model = self.table.selectionModel()
        self._syncing = True
        try:
            selection_model.clearSelection()
            for source_row in valid:
                row = self._model.row_for_source_index(source_row)
                if row is None:
                    continue
                selection_model.select(
                    self._model.index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
            if current_index is not None:
                row = self._model.row_for_source_index(current_index)
                if row is not None:
                    self.table.setCurrentIndex(self._model.index(row, 0))
                    self._queue_preview_row(row)
        finally:
            self._syncing = False

    def _handle_current_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if self._syncing:
            return
        row = current.row()
        source_row = self._model.source_index_at(row)
        if source_row is not None:
            self.current_changed.emit(source_row)
            self._queue_preview_row(row)

    def _handle_selection_changed(self, *_args) -> None:
        if self._syncing:
            return
        self.selection_changed.emit()

    def _show_context_menu(self, point) -> None:
        index = self.table.indexAt(point)
        if not index.isValid():
            return
        source_row = self._model.source_index_at(index.row())
        if source_row is not None:
            self.context_menu_requested.emit(source_row, self.table.viewport().mapToGlobal(point))

    def _queue_preview_row(self, row: int, *, immediate: bool = False) -> None:
        if not self.preview_pane.isVisible():
            return
        self._pending_preview_row = row
        if immediate:
            self._preview_update_timer.stop()
            self._apply_pending_preview_row()
            return
        self._preview_update_timer.start()

    def _apply_pending_preview_row(self) -> None:
        if not self.preview_pane.isVisible():
            return
        self.preview_pane.set_record(self._model.record_at(self._pending_preview_row))
