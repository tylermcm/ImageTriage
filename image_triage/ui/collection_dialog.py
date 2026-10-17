from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .collection_ring import COLLECTION_COLORS, collection_ring_pixmap, normalized_color


COLLECTION_EXPLANATION = (
    "Collections reference images where they already live; no files are moved or "
    "duplicated. Use them to assemble portfolios, edit queues, proofing sets, or themes."
)


@dataclass(slots=True, frozen=True)
class CollectionDialogResult:
    name: str
    kind: str
    description: str = ""
    color: str = ""
    add_new_winners: bool = False


class CollectionEditDialog(QDialog):
    """New Collection / Edit Collection: a name, a color for the sidebar ring, and whether new winners go
    in. It creates an empty collection; the hints at the bottom say how to fill it
    (docs/winners_collections_redesign.md). Purpose and description are kept as stored, no longer asked."""

    def __init__(
        self,
        *,
        title: str = "New Collection",
        name: str = "",
        kind: str = "Custom",
        description: str = "",
        selection_count: int = 0,
        color: str = "",
        add_new_winners: bool = True,
        existing_names: tuple[str, ...] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        del selection_count  # the window always creates or edits the collection itself, never its photos
        self.setObjectName("collectionEditDialog")
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self._kind = kind or "Custom"
        self._description = description
        self._existing_names = {" ".join(item.split()).casefold() for item in existing_names if item}
        editing = bool(name)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(0)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("collectionDialogTitle")
        self.title_label.setToolTip(COLLECTION_EXPLANATION)
        layout.addWidget(self.title_label)
        layout.addSpacing(16)

        self.name_label = self._field_label("Name")
        layout.addWidget(self.name_label)
        layout.addSpacing(5)
        self.name_field = QLineEdit(name, self)
        self.name_field.setObjectName("collectionNameField")
        self.name_field.setPlaceholderText("e.g. Utah Portfolio")
        layout.addWidget(self.name_field)
        self.name_error = QLabel("", self)
        self.name_error.setObjectName("collectionNameError")
        self.name_error.setStyleSheet("color: #e0445a;")
        self.name_error.hide()
        layout.addWidget(self.name_error)
        layout.addSpacing(14)

        self.color_label = self._field_label("Color")
        layout.addWidget(self.color_label)
        layout.addSpacing(6)
        self._color = normalized_color(color)
        self._swatch_group = QButtonGroup(self)
        self._swatch_group.setExclusive(True)
        self._swatch_row = QHBoxLayout()
        self._swatch_row.setContentsMargins(0, 0, 0, 0)
        self._swatch_row.setSpacing(6)
        self._swatches: dict[str, QToolButton] = {}
        self._add_swatch("", "No color")
        for label, value in COLLECTION_COLORS:
            self._add_swatch(value, label)
        self._custom_swatch: QToolButton | None = None
        if self._color and self._color not in self._swatches:
            self._custom_swatch = self._add_swatch(self._color, "Custom color")
        self.more_colors_button = QPushButton("More...", self)
        self.more_colors_button.setObjectName("collectionMoreColorsButton")
        self.more_colors_button.setAutoDefault(False)
        self.more_colors_button.clicked.connect(self._choose_custom_color)
        self._swatch_row.addWidget(self.more_colors_button)
        self._swatch_row.addStretch(1)
        layout.addLayout(self._swatch_row)
        self._swatches.get(self._color, self._swatches[""]).setChecked(True)
        layout.addSpacing(16)

        self.add_winners_check = QCheckBox("Add new winners here", self)
        self.add_winners_check.setObjectName("collectionAddWinnersCheck")
        self.add_winners_check.setChecked(bool(add_new_winners))
        layout.addWidget(self.add_winners_check)
        self.add_winners_hint = QLabel("Every winner you mark with W also goes in.", self)
        self.add_winners_hint.setObjectName("secondaryText")
        self.add_winners_hint.setContentsMargins(24, 0, 0, 0)
        layout.addWidget(self.add_winners_hint)
        layout.addSpacing(14)

        self.fill_hints = QLabel(self)
        self.fill_hints.setObjectName("secondaryText")
        self.fill_hints.setWordWrap(True)
        self.fill_hints.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.fill_hints)
        layout.addStretch(1)

        actions = QWidget(self)
        actions.setObjectName("collectionDialogActions")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 16, 0, 0)
        action_layout.setSpacing(8)
        action_layout.addStretch(1)
        self.cancel_button = QPushButton("Cancel", actions)
        self.cancel_button.setObjectName("collectionCancelButton")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        action_layout.addWidget(self.cancel_button)
        self.primary_button = QPushButton("Save" if editing else "Create", actions)
        self.primary_button.setObjectName("collectionPrimaryButton")
        self.primary_button.setDefault(True)
        self.primary_button.clicked.connect(self._accept_if_valid)
        action_layout.addWidget(self.primary_button)
        layout.addWidget(actions)

        self.name_field.textChanged.connect(self._sync_primary_enabled)
        self.add_winners_check.toggled.connect(self._sync_fill_hints)
        self._sync_primary_enabled()
        self._sync_fill_hints()
        self.name_field.setFocus()
        self.name_field.selectAll()

    def _add_swatch(self, value: str, label: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("collectionColorSwatch")
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setProperty("colorValue", value)
        button.setIcon(QIcon(collection_ring_pixmap(value, 18)))
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(28, 28)
        button.toggled.connect(lambda checked, target=value: self._pick_color(target) if checked else None)
        self._swatch_group.addButton(button)
        index = max(0, self._swatch_row.count() - (2 if hasattr(self, "more_colors_button") else 0))
        self._swatch_row.insertWidget(index, button)
        self._swatches[value] = button
        return button

    def _pick_color(self, value: str) -> None:
        self._color = value

    def _choose_custom_color(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color or "#4c8dff"), self, "Collection Color")
        if not chosen.isValid():
            return
        value = chosen.name()
        if value not in self._swatches:
            if self._custom_swatch is not None:
                self._swatches.pop(str(self._custom_swatch.property("colorValue")), None)
                self._swatch_group.removeButton(self._custom_swatch)
                self._custom_swatch.deleteLater()
            self._custom_swatch = self._add_swatch(value, "Custom color")
        self._swatches[value].setChecked(True)

    def _sync_fill_hints(self, *_args) -> None:
        on = self.add_winners_check.isChecked()
        w_line = "Mark a winner and it goes in" if on else "Marks a winner only, unless <i>Add new winners here</i> is on"
        rows = (
            ("W", w_line),
            ("B", "Add the photo you're on without marking it a winner"),
            ("Add All bar", "Filter or select photos, then Add All to Collection above the grid"),
            ("Drag", "Drop photos onto it in the sidebar"),
            ("Right-click", "On a photo: Collections, then tick it"),
        )
        cells = "".join(
            f"<tr><td style='padding:1px 10px 1px 0'><b>{how}</b></td><td style='padding:1px 0'>{text}</td></tr>"
            for how, text in rows
        )
        self.fill_hints.setText(f"<table cellspacing='0'>{cells}</table>")

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("collectionFieldLabel")
        return label

    def _name_problem(self) -> str:
        name = " ".join(self.name_field.text().split())
        if not name:
            return "Give it a name."
        if name.casefold() in self._existing_names:
            return f"You already have a collection called “{name}”."
        return ""

    def _sync_primary_enabled(self, *_args) -> None:
        problem = self._name_problem()
        self.primary_button.setEnabled(not problem)
        show = bool(problem) and bool(self.name_field.text().strip())
        self.name_error.setText(problem)
        self.name_error.setVisible(show)

    def result_data(self) -> CollectionDialogResult:
        return CollectionDialogResult(
            name=" ".join(self.name_field.text().split()),
            kind=self._kind,
            description=self._description,
            color=self._color,
            add_new_winners=self.add_winners_check.isChecked(),
        )

    def _accept_if_valid(self) -> None:
        if self._name_problem():
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
