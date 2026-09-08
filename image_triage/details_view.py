from __future__ import annotations

from datetime import datetime
import re
import time
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QByteArray, QItemSelectionModel, QModelIndex, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .formats import suffix_for_path
from .models import ImageRecord, SessionAnnotation
from .perf import perf_logger


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


def _natural_name_key(value: str) -> tuple[tuple[int, object], ...]:
    parts = re.split(r"(\d+)", value.casefold())
    key: list[tuple[int, object]] = []
    for part in parts:
        if not part:
            continue
        key.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(key)


class DetailsTableModel(QAbstractTableModel):
    COLUMNS = ("Name", "Decision", "AI", "Date modified", "Type", "Size")

    def __init__(self, *, ai_text_provider: Callable[[ImageRecord], str], parent=None) -> None:
        super().__init__(parent)
        self._records: list[ImageRecord] = []
        self._rows: list[int] = []
        self._row_position_by_source: dict[int, int] = {}
        self._static_display_cache: list[tuple[str, str, str]] = []
        self._ai_display_cache: dict[str, str] = {}
        self._annotations: dict[str, SessionAnnotation] = {}
        self._ai_text_provider = ai_text_provider
        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder

    def set_records(self, records: list[ImageRecord]) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        self.beginResetModel()
        self._records = list(records)
        self._static_display_cache = [(_format_modified(record.modified_ns), _file_type(record), "" if record.is_folder else _format_bytes(record.size)) for record in self._records]
        self._ai_display_cache.clear()
        self._rows = list(range(len(self._records)))
        self._sort_rows()
        self._rebuild_row_positions()
        self.endResetModel()
        if logger.enabled:
            logger.duration("details.model.set_records", (time.perf_counter() - start) * 1000.0, count=len(records))

    def append_records(self, records: list[ImageRecord]) -> None:
        if not records:
            return
        self.beginResetModel()
        self._records.extend(records)
        self._static_display_cache.extend(
            (_format_modified(record.modified_ns), _file_type(record), "" if record.is_folder else _format_bytes(record.size))
            for record in records
        )
        self._ai_display_cache.clear()
        self._rows = list(range(len(self._records)))
        self._sort_rows()
        self._rebuild_row_positions()
        self.endResetModel()

    def set_annotations(self, annotations: dict[str, SessionAnnotation]) -> None:
        self._annotations = annotations
        if self._sort_column == 1:
            self.layoutAboutToBeChanged.emit()
            self._sort_rows()
            self._rebuild_row_positions()
            self.layoutChanged.emit()
        self.refresh_rows()

    def refresh_rows(self, rows: set[int] | None = None) -> None:
        if not self._records:
            return
        if rows:
            for source_row in rows:
                record = self.record_for_source_index(source_row)
                if record is not None:
                    self._ai_display_cache.pop(record.path, None)
                row = self.row_for_source_index(source_row)
                if row is not None:
                    self.dataChanged.emit(self.index(row, 0), self.index(row, len(self.COLUMNS) - 1))
            return
        self._ai_display_cache.clear()
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
            if column == 5:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == 0:
            return record.name
        if column == 1:
            return _decision_text(annotation)
        if column == 2:
            return "-" if record.is_folder else self._ai_text(record)
        if column == 3:
            return self._static_display_cache[source_row][0]
        if column == 4:
            return self._static_display_cache[source_row][1]
        if column == 5:
            return self._static_display_cache[source_row][2]
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
        return self._row_position_by_source.get(source_index)

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        if not 0 <= column < len(self.COLUMNS):
            return
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        self.layoutAboutToBeChanged.emit()
        self._sort_column = column
        self._sort_order = order
        self._sort_rows()
        self._rebuild_row_positions()
        self.layoutChanged.emit()
        if logger.enabled:
            logger.duration("details.model.sort", (time.perf_counter() - start) * 1000.0, column=column, order="desc" if order == Qt.SortOrder.DescendingOrder else "asc", count=len(self._records))

    def sort_column(self) -> int:
        return self._sort_column

    def sort_order(self) -> Qt.SortOrder:
        return self._sort_order

    def _sort_rows(self) -> None:
        reverse = self._sort_order == Qt.SortOrder.DescendingOrder

        def sort_value(source_row: int):
            record = self._records[source_row]
            annotation = self._annotations.get(record.path)
            if self._sort_column == 1:
                return _decision_text(annotation).casefold()
            if self._sort_column == 2:
                return "" if record.is_folder else self._ai_text(record).casefold()
            if self._sort_column == 3:
                return record.modified_ns
            if self._sort_column == 4:
                return self._static_display_cache[source_row][1].casefold()
            if self._sort_column == 5:
                return record.size
            return _natural_name_key(record.name)

        folders = [row for row in self._rows if self._records[row].is_folder]
        files = [row for row in self._rows if not self._records[row].is_folder]
        folders.sort(key=lambda row: _natural_name_key(self._records[row].name), reverse=reverse)
        files.sort(key=sort_value, reverse=reverse)
        self._rows = folders + files
        self._rebuild_row_positions()

    def _rebuild_row_positions(self) -> None:
        self._row_position_by_source = {source_row: row for row, source_row in enumerate(self._rows)}

    def _ai_text(self, record: ImageRecord) -> str:
        cached = self._ai_display_cache.get(record.path)
        if cached is not None:
            return cached
        value = self._ai_text_provider(record)
        text = value if isinstance(value, str) and value else "-"
        self._ai_display_cache[record.path] = text
        return text


class DetailsTableView(QTableView):
    preview_requested = Signal(int)
    delete_requested = Signal(int)
    keep_requested = Signal(int)
    move_requested = Signal(int)
    tag_requested = Signal(int)
    winner_requested = Signal(int)
    reject_requested = Signal(int)
    filename_prefix_requested = Signal(str)
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
        if key == Qt.Key.Key_T and review_allowed:
            self.tag_requested.emit(row)
            return
        if key == Qt.Key.Key_W and review_allowed:
            self.winner_requested.emit(row)
            return
        if key == Qt.Key.Key_X and review_allowed:
            self.reject_requested.emit(row)
            return
        text = event.text()
        reserved = {
            Qt.Key.Key_C,
            Qt.Key.Key_K,
            Qt.Key.Key_M,
            Qt.Key.Key_T,
            Qt.Key.Key_W,
            Qt.Key.Key_X,
        }
        if review_allowed and key not in reserved and text and text.strip() and text.isprintable():
            self.filename_prefix_requested.emit(text.casefold())
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        super().wheelEvent(event)
        if logger.enabled:
            logger.duration("details.table.wheel", (time.perf_counter() - start) * 1000.0, value=self.verticalScrollBar().value())


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


class PhotoDetailsView(QWidget):
    current_changed = Signal(int)
    selection_changed = Signal()
    layout_state_changed = Signal()
    preview_requested = Signal(int)
    context_menu_requested = Signal(int, object)
    delete_requested = Signal(int)
    keep_requested = Signal(int)
    move_requested = Signal(int)
    tag_requested = Signal(int)
    winner_requested = Signal(int)
    reject_requested = Signal(int)

    def __init__(
        self,
        *,
        ai_text_provider: Callable[[ImageRecord], str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._syncing = False
        self._row_density = "comfortable"
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
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().hide()
        header = DetailsHeaderView(Qt.Orientation.Horizontal, self.table)
        self.table.setHorizontalHeader(header)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        if hasattr(header, "setResizeContentsPrecision"):
            header.setResizeContentsPrecision(64)
        column_widths = {
            1: 112,
            2: 76,
            3: 142,
            4: 96,
            5: 88,
        }
        for column, width in column_widths.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(column, width)
        self.table.doubleClicked.connect(lambda index: self._emit_row_signal(self.preview_requested, index.row()) if index.isValid() else None)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.filename_prefix_requested.connect(self._jump_to_filename_prefix)
        header.sortIndicatorChanged.connect(lambda _section, _order: self._handle_sort_changed())
        header.sectionResized.connect(lambda *_args: self.layout_state_changed.emit())

        selection_model = self.table.selectionModel()
        selection_model.currentRowChanged.connect(self._handle_current_row_changed)
        selection_model.selectionChanged.connect(self._handle_selection_changed)

        self.table.preview_requested.connect(lambda row: self._emit_row_signal(self.preview_requested, row))
        self.table.delete_requested.connect(lambda row: self._emit_row_signal(self.delete_requested, row))
        self.table.keep_requested.connect(lambda row: self._emit_row_signal(self.keep_requested, row))
        self.table.move_requested.connect(lambda row: self._emit_row_signal(self.move_requested, row))
        self.table.tag_requested.connect(lambda row: self._emit_row_signal(self.tag_requested, row))
        self.table.winner_requested.connect(lambda row: self._emit_row_signal(self.winner_requested, row))
        self.table.reject_requested.connect(lambda row: self._emit_row_signal(self.reject_requested, row))

        self.status_label = QLabel("")
        self.status_label.setObjectName("detailsStatusStrip")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status_label)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.set_row_density(self._row_density)
        self._refresh_status()

    def set_records(self, records: list[ImageRecord]) -> None:
        self._model.set_records(records)
        self._refresh_status()

    def append_records(self, records: list[ImageRecord]) -> None:
        self._model.append_records(records)
        self._refresh_status()

    def set_annotations(self, annotations: dict[str, SessionAnnotation]) -> None:
        self._model.set_annotations(annotations)
        self._refresh_status()

    def refresh_rows(self, rows: set[int] | None = None) -> None:
        self._model.refresh_rows(rows)

    def set_row_density(self, density: str) -> None:
        normalized = density if density in {"compact", "comfortable"} else "comfortable"
        self._row_density = normalized
        row_height = 22 if normalized == "compact" else 30
        header_height = 34 if normalized == "compact" else 38
        header = self.table.horizontalHeader()
        header.setMinimumHeight(header_height)
        self.table.verticalHeader().setDefaultSectionSize(row_height)
        self.table.setIconSize(QSize(row_height - 4, row_height - 4))
        self._refresh_status()

    def row_density(self) -> str:
        return self._row_density

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

    def set_current_index(self, index: int) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        row = self._model.row_for_source_index(index)
        if row is None:
            return
        self._syncing = True
        try:
            model_index = self._model.index(row, 0)
            self.table.setCurrentIndex(model_index)
            self.table.scrollTo(model_index, QAbstractItemView.ScrollHint.EnsureVisible)
        finally:
            self._syncing = False
        if logger.enabled:
            logger.duration("details.set_current_index", (time.perf_counter() - start) * 1000.0, index=index, row=row)

    def set_selected_indexes(self, indexes: list[int], *, current_index: int | None = None) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
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
        finally:
            self._syncing = False
        self._refresh_status()
        if logger.enabled:
            logger.duration("details.set_selected_indexes", (time.perf_counter() - start) * 1000.0, requested=len(indexes), valid=len(valid), current_index=current_index)

    def _handle_current_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if self._syncing:
            return
        row = current.row()
        source_row = self._model.source_index_at(row)
        if source_row is not None:
            self.current_changed.emit(source_row)
        self._refresh_status()

    def _handle_selection_changed(self, *_args) -> None:
        if self._syncing:
            return
        self.selection_changed.emit()
        self._refresh_status()

    def _show_context_menu(self, point) -> None:
        index = self.table.indexAt(point)
        if not index.isValid():
            return
        source_row = self._model.source_index_at(index.row())
        if source_row is not None:
            self.context_menu_requested.emit(source_row, self.table.viewport().mapToGlobal(point))

    def _handle_sort_changed(self) -> None:
        self.layout_state_changed.emit()
        self._refresh_status()

    def _jump_to_filename_prefix(self, prefix: str) -> None:
        if not prefix:
            return
        row_count = self._model.rowCount()
        if row_count <= 0:
            return
        start = self.table.currentIndex().row()
        for offset in range(1, row_count + 1):
            row = (max(0, start) + offset) % row_count
            record = self._model.record_at(row)
            if record is not None and record.name.casefold().startswith(prefix):
                source_row = self._model.source_index_at(row)
                if source_row is not None:
                    self.set_selected_indexes([source_row], current_index=source_row)
                    self.current_changed.emit(source_row)
                return

    def _refresh_status(self) -> None:
        total = self._model.rowCount()
        selected = len(self.selected_indexes())
        sort_column = self._model.sort_column()
        sort_name = DetailsTableModel.COLUMNS[sort_column] if 0 <= sort_column < len(DetailsTableModel.COLUMNS) else "Name"
        order = "ascending" if self._model.sort_order() == Qt.SortOrder.AscendingOrder else "descending"
        parts = [f"{total} item{'s' if total != 1 else ''}"]
        if selected:
            parts.insert(0, f"{selected} selected")
        parts.append(f"sorted by {sort_name} ({order})")
        self.status_label.setText("  |  ".join(parts))

    def save_header_state(self) -> QByteArray:
        return self.table.horizontalHeader().saveState()

    def restore_header_state(self, state: QByteArray | None) -> None:
        if isinstance(state, QByteArray) and not state.isEmpty():
            self.table.horizontalHeader().restoreState(state)

    def sort_state(self) -> tuple[int, Qt.SortOrder]:
        return self._model.sort_column(), self._model.sort_order()

    def set_sort_state(self, column: int, order: Qt.SortOrder) -> None:
        if not 0 <= column < len(DetailsTableModel.COLUMNS):
            column = 0
        self.table.sortByColumn(column, order)
        self._refresh_status()
