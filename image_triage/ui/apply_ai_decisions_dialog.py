from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..models import ImageRecord
from ..thumbnails import ThumbnailManager

# Small, square, review-grade thumbnails - big enough to recognize a photo at
# a glance, far smaller than anything the main grid or filmstrip decode (this
# dialog can be showing hundreds at once, not one page of a few dozen).
THUMBNAIL_SIZE = QSize(96, 96)
_GRID_COLUMNS = 6


class ApplyAIDecisionsDialog(QDialog):
    """Reviewable confirmation for "Apply AI Decisions".

    Shows the AI Pick and Reject groups as thumbnail grids (grouped under
    their own headers) so the user can visually scan what is about to move
    before committing, instead of trusting a bare count in a QMessageBox.
    Keeper/Needs Review images are not moving, so they are summarized as
    text only, same as before.

    Thumbnails are loaded through the app's shared ``ThumbnailManager`` -
    memory/disk cached and decoded off the UI thread - so opening this
    dialog with a few hundred records never blocks on synchronous decodes.
    Already-cached thumbnails (typically true here, since these records were
    just browsed in the main grid) paint immediately; anything else arrives
    asynchronously via the ``thumbnail_ready`` signal as it finishes.
    """

    def __init__(
        self,
        *,
        ai_pick_records: list[ImageRecord],
        reject_records: list[ImageRecord],
        keeper_count: int,
        review_count: int,
        thumbnail_manager: ThumbnailManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Apply AI Decisions")
        self.setModal(True)
        self.resize(820, 680)

        self.ai_pick_records = list(ai_pick_records)
        self.reject_records = list(reject_records)
        self.keeper_count = int(keeper_count)
        self.review_count = int(review_count)

        self._thumbnail_manager = thumbnail_manager
        self._pending_labels: dict[object, list[QLabel]] = {}
        self._thumbnail_manager.thumbnail_ready.connect(self._on_thumbnail_ready)
        self.finished.connect(self._disconnect_thumbnail_manager)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QLabel(
            "This will organize the current folder using the loaded AI review.\n\n"
            f"- Move {len(self.ai_pick_records)} AI Pick image(s) into _winners\n"
            f"- Move {len(self.reject_records)} Reject image(s) into the program recycle bin\n"
            f"- Leave {self.keeper_count} Keeper image(s) and {self.review_count} Needs Review "
            "image(s) for manual follow-up",
            self,
        )
        header.setWordWrap(True)
        root.addWidget(header)

        if self.ai_pick_records:
            root.addWidget(
                self._build_section(f"AI Pick → _winners ({len(self.ai_pick_records)})", self.ai_pick_records),
                1,
            )
        if self.reject_records:
            root.addWidget(
                self._build_section(f"Reject → Recycle ({len(self.reject_records)})", self.reject_records),
                1,
            )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # -- layout --------------------------------------------------------

    def _build_section(self, title: str, records: list[ImageRecord]) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        heading = QLabel(title, container)
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)

        scroll = QScrollArea(container)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(220)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(10)
        for index, record in enumerate(records):
            grid.addWidget(self._build_cell(record), index // _GRID_COLUMNS, index % _GRID_COLUMNS)
        grid.setRowStretch(len(records) // _GRID_COLUMNS + 1, 1)
        scroll.setWidget(grid_host)
        layout.addWidget(scroll, 1)
        return container

    def _build_cell(self, record: ImageRecord) -> QWidget:
        cell = QWidget()
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(2, 2, 2, 2)
        cell_layout.setSpacing(2)

        thumb_label = QLabel(cell)
        thumb_label.setFixedSize(THUMBNAIL_SIZE)
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_label.setStyleSheet("background: palette(base); border: 1px solid palette(mid);")
        cell_layout.addWidget(thumb_label)

        name_label = QLabel(cell)
        metrics = QFontMetrics(name_label.font())
        name_label.setText(metrics.elidedText(record.name, Qt.TextElideMode.ElideMiddle, THUMBNAIL_SIZE.width()))
        name_label.setToolTip(record.name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cell_layout.addWidget(name_label)

        self._load_thumbnail(record, thumb_label)
        return cell

    # -- thumbnails ------------------------------------------------------

    def _load_thumbnail(self, record: ImageRecord, label: QLabel) -> None:
        cached = self._thumbnail_manager.get_cached(record, THUMBNAIL_SIZE)
        if cached is not None and not cached.isNull():
            label.setPixmap(QPixmap.fromImage(cached))
            return
        key = self._thumbnail_manager.request_thumbnail(
            record,
            THUMBNAIL_SIZE,
            priority=10_000,
            drop_if_not_wanted=False,
        )
        self._pending_labels.setdefault(key, []).append(label)

    def _on_thumbnail_ready(self, key, image) -> None:
        labels = self._pending_labels.pop(key, None)
        if not labels:
            return
        pixmap = QPixmap.fromImage(image)
        for label in labels:
            try:
                label.setPixmap(pixmap)
            except RuntimeError:
                # The underlying C++ widget can already be gone if the dialog
                # closed while a request was still in flight.
                pass

    def _disconnect_thumbnail_manager(self, *_args) -> None:
        try:
            self._thumbnail_manager.thumbnail_ready.disconnect(self._on_thumbnail_ready)
        except (RuntimeError, TypeError):
            pass
