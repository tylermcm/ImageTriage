from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
)

from ..library_store import COLLECTION_KINDS


@dataclass(slots=True, frozen=True)
class CollectionDialogResult:
    name: str
    kind: str
    description: str = ""


class CollectionEditDialog(QDialog):
    def __init__(
        self,
        *,
        title: str = "Create Virtual Collection",
        name: str = "",
        kind: str = "Custom",
        description: str = "",
        selection_count: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro = QLabel(
            "Virtual collections are non-destructive sets. They reference files where they already live so you can assemble portfolios, edit queues, proofing groups, or themes without moving anything."
        )
        intro.setWordWrap(True)
        intro.setObjectName("secondaryText")
        layout.addWidget(intro)

        if selection_count > 0:
            count_label = QLabel(f"{selection_count} selected image bundle(s) will be added.")
            count_label.setObjectName("secondaryText")
            layout.addWidget(count_label)

        form = QFormLayout()
        form.setSpacing(10)
        layout.addLayout(form)

        self.name_field = QLineEdit(name)
        form.addRow("Name", self.name_field)

        self.kind_combo = QComboBox()
        for item in COLLECTION_KINDS:
            self.kind_combo.addItem(item, item)
        index = self.kind_combo.findData(kind)
        if index >= 0:
            self.kind_combo.setCurrentIndex(index)
        form.addRow("Purpose", self.kind_combo)

        self.description_field = QTextEdit()
        self.description_field.setPlaceholderText("Optional notes for this set")
        self.description_field.setPlainText(description)
        self.description_field.setMaximumHeight(100)
        form.addRow("Description", self.description_field)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
