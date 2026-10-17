from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QBrush, QPainter, QPalette, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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


@dataclass(slots=True, frozen=True)
class CollectionPickerChoice:
    """What the picker returned: an existing collection id, or a name to create."""
    collection_id: str = ""
    new_name: str = ""


class CollectionPickerDialog(QDialog):
    """Pick a collection by typing: recently used first, then alphabetical, and
    "New collection" (named from the search text) at the bottom.

    ``entries`` are ``(collection_id, name, item_count)`` in display order."""

    NEW_ROLE = "__new__"

    def __init__(
        self,
        entries: list[tuple[str, str, int]],
        *,
        title: str,
        default_id: str = "",
        recent_ids: tuple[str, ...] = (),
        allow_new: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("collectionPickerDialog")
        self._allow_new = allow_new
        self.setWindowTitle(title)
        self.resize(380, 420)
        self._entries = list(entries)
        self._recent_ids = set(recent_ids)
        self._choice = CollectionPickerChoice()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        self.search_field = QLineEdit(self)
        self.search_field.setObjectName("collectionPickerSearch")
        self.search_field.setPlaceholderText("Type to find or name a new collection")
        self.search_field.setClearButtonEnabled(True)
        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("collectionPickerList")
        layout.addWidget(self.search_field)
        layout.addWidget(self.list_widget, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        self.primary_button = QPushButton("Choose", self)
        self.primary_button.setDefault(True)
        buttons.addWidget(cancel)
        buttons.addWidget(self.primary_button)
        layout.addLayout(buttons)

        cancel.clicked.connect(self.reject)
        self.primary_button.clicked.connect(self._accept_current)
        self.list_widget.itemActivated.connect(lambda _item: self._accept_current())
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._accept_current())
        self.search_field.textChanged.connect(self._populate)
        self.search_field.returnPressed.connect(self._accept_current)
        self.search_field.installEventFilter(self)
        self._populate()
        self._select_id(default_id)
        self.search_field.setFocus()

    def choice(self) -> CollectionPickerChoice:
        return self._choice

    def _populate(self, *_args) -> None:
        text = " ".join(self.search_field.text().split())
        needle = text.casefold()
        previous = self._current_id()
        self.list_widget.clear()
        exact = False
        for collection_id, name, count in self._entries:
            if needle and needle not in name.casefold():
                continue
            exact = exact or name.casefold() == needle
            label = f"{name}  ({count})"
            if collection_id in self._recent_ids:
                label += "  · recent"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, collection_id)
            self.list_widget.addItem(item)
        if self._allow_new:
            new_label = f'New collection "{text}"' if text and not exact else "New collection..."
            new_item = QListWidgetItem(new_label)
            new_item.setData(Qt.ItemDataRole.UserRole, self.NEW_ROLE)
            self.list_widget.addItem(new_item)
        if not self._select_id(previous):
            self.list_widget.setCurrentRow(0)

    def _select_id(self, collection_id: str) -> bool:
        if not collection_id:
            return False
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).data(Qt.ItemDataRole.UserRole) == collection_id:
                self.list_widget.setCurrentRow(row)
                return True
        return False

    def _current_id(self) -> str:
        item = self.list_widget.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _accept_current(self) -> None:
        current = self._current_id()
        if not current:
            return
        if current == self.NEW_ROLE:
            self._choice = CollectionPickerChoice(new_name=" ".join(self.search_field.text().split()))
        else:
            self._choice = CollectionPickerChoice(collection_id=current)
        self.accept()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        # Up/Down in the search box move through the list, so the keyboard never leaves the field.
        if watched is self.search_field and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                row = self.list_widget.currentRow() + (1 if event.key() == Qt.Key.Key_Down else -1)
                if 0 <= row < self.list_widget.count():
                    self.list_widget.setCurrentRow(row)
                return True
        return super().eventFilter(watched, event)
