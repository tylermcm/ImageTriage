from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QPainter, QPalette, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..library_store import COLLECTION_KINDS


COLLECTION_EXPLANATION = (
    "Collections reference images where they already live; no files are moved or "
    "duplicated. Use them to assemble portfolios, edit queues, proofing sets, or themes."
)


@dataclass(slots=True, frozen=True)
class CollectionDialogResult:
    name: str
    kind: str
    description: str = ""


class _PurposeComboBox(QComboBox):
    """Purpose picker with an explicit disclosure affordance on every style."""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = self.palette().color(QPalette.ColorRole.Text)
        colour.setAlpha(205)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(colour))
        center_x = self.width() - 16
        center_y = self.height() / 2.0
        painter.drawPolygon(
            QPolygonF(
                (
                    QPointF(center_x - 4, center_y - 2),
                    QPointF(center_x + 4, center_y - 2),
                    QPointF(center_x, center_y + 2),
                )
            )
        )


class CollectionEditDialog(QDialog):
    def __init__(
        self,
        *,
        title: str = "Create Collection",
        name: str = "",
        kind: str = "Custom",
        description: str = "",
        selection_count: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("collectionEditDialog")
        self.setWindowTitle(title)
        self.resize(460, 350)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(0)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("collectionDialogTitle")
        self.title_label.setToolTip(COLLECTION_EXPLANATION)
        layout.addWidget(self.title_label)
        layout.addSpacing(10)

        self.target_badge = QLabel(self._target_text(selection_count, editing=bool(name)), self)
        self.target_badge.setObjectName("collectionTargetBadge")
        self.target_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.target_badge.setToolTip(COLLECTION_EXPLANATION)
        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.addWidget(self.target_badge)
        target_row.addStretch(1)
        layout.addLayout(target_row)
        layout.addSpacing(16)

        self.name_label = self._field_label("Name")
        layout.addWidget(self.name_label)
        layout.addSpacing(5)
        self.name_field = QLineEdit(name, self)
        self.name_field.setObjectName("collectionNameField")
        self.name_field.setPlaceholderText("e.g., Portfolio Maybes")
        layout.addWidget(self.name_field)
        layout.addSpacing(12)

        self.purpose_label = self._field_label("Purpose")
        layout.addWidget(self.purpose_label)
        layout.addSpacing(5)
        self.kind_combo = _PurposeComboBox(self)
        self.kind_combo.setObjectName("collectionPurposeCombo")
        for item in COLLECTION_KINDS:
            self.kind_combo.addItem(item, item)
        index = self.kind_combo.findData(kind)
        if index >= 0:
            self.kind_combo.setCurrentIndex(index)
        layout.addWidget(self.kind_combo)
        layout.addSpacing(12)

        self.description_label = self._field_label("Description")
        layout.addWidget(self.description_label)
        layout.addSpacing(5)
        self.description_field = QTextEdit(self)
        self.description_field.setObjectName("collectionDescriptionField")
        self.description_field.setAcceptRichText(False)
        self.description_field.setPlaceholderText("Optional notes")
        self.description_field.setPlainText(description)
        self.description_field.setFixedHeight(72)
        layout.addWidget(self.description_field)
        layout.addStretch(1)

        actions = QWidget(self)
        actions.setObjectName("collectionDialogActions")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 14, 0, 0)
        action_layout.setSpacing(8)
        action_layout.addStretch(1)
        self.cancel_button = QPushButton("Cancel", actions)
        self.cancel_button.setObjectName("collectionCancelButton")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        action_layout.addWidget(self.cancel_button)
        primary_text = (
            "Create Collection"
            if title.strip().lower().startswith("create")
            else "Save Changes"
        )
        self.primary_button = QPushButton(primary_text, actions)
        self.primary_button.setObjectName("collectionPrimaryButton")
        self.primary_button.setDefault(True)
        self.primary_button.clicked.connect(self._accept_if_valid)
        action_layout.addWidget(self.primary_button)
        layout.addWidget(actions)

        self.name_field.textChanged.connect(self._sync_primary_enabled)
        self._sync_primary_enabled()
        self.name_field.setFocus()
        self.name_field.selectAll()

    @staticmethod
    def _target_text(selection_count: int, *, editing: bool) -> str:
        count = max(0, int(selection_count))
        if count:
            noun = "bundle" if count == 1 else "bundles"
            return f"Target: {count} selected {noun}"
        return "Target: Existing collection" if editing else "Target: Empty collection"

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("collectionFieldLabel")
        return label

    def _sync_primary_enabled(self, *_args) -> None:
        self.primary_button.setEnabled(bool(" ".join(self.name_field.text().split())))

    def result_data(self) -> CollectionDialogResult:
        return CollectionDialogResult(
            name=" ".join(self.name_field.text().split()),
            kind=str(self.kind_combo.currentData() or "Custom"),
            description=self.description_field.toPlainText().strip(),
        )

    def _accept_if_valid(self) -> None:
        if not " ".join(self.name_field.text().split()):
            self.name_field.setFocus()
            return
        self.accept()
