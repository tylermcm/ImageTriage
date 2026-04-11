from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QKeySequenceEdit,
    QVBoxLayout,
    QWidget,
)

from ..keyboard_mapping import ShortcutBinding, shortcut_conflicts


class KeyboardShortcutDialog(QDialog):
    def __init__(self, bindings: list[ShortcutBinding], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(860, 560)
        self._initial_bindings = [replace(binding) for binding in bindings]
        self._bindings = [replace(binding) for binding in bindings]

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        intro = QLabel(
            "Customize the main workstation shortcuts without changing users who prefer the defaults. Conflicting shortcuts are blocked before you can apply them."
        )
        intro.setWordWrap(True)
        intro.setObjectName("secondaryText")
        root_layout.addWidget(intro)

        content_row = QHBoxLayout()
        content_row.setSpacing(14)
        root_layout.addLayout(content_row, 1)

        self.binding_list = QListWidget(self)
        self.binding_list.currentRowChanged.connect(self._load_binding_row)
        content_row.addWidget(self.binding_list, 1)

        editor = QWidget(self)
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(10)
        content_row.addWidget(editor, 1)

        self.section_label = QLabel("")
        self.section_label.setObjectName("sectionLabel")
        editor_layout.addWidget(self.section_label)

        self.title_label = QLabel("")
        self.title_label.setWordWrap(True)
        editor_layout.addWidget(self.title_label)

        self.default_label = QLabel("")
        self.default_label.setObjectName("secondaryText")
        editor_layout.addWidget(self.default_label)

        self.shortcut_edit = QKeySequenceEdit(self)
        self.shortcut_edit.keySequenceChanged.connect(self._handle_shortcut_changed)
        editor_layout.addWidget(self.shortcut_edit)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        editor_layout.addLayout(button_row)

        self.clear_button = QPushButton("Clear")
        self.reset_button = QPushButton("Reset To Default")
        self.reset_all_button = QPushButton("Reset All")
        self.clear_button.clicked.connect(self._clear_selected_binding)
        self.reset_button.clicked.connect(self._reset_selected_binding)
        self.reset_all_button.clicked.connect(self._reset_all_bindings)
        button_row.addWidget(self.clear_button)
        button_row.addWidget(self.reset_button)
        button_row.addWidget(self.reset_all_button)
        button_row.addStretch(1)

        self.conflict_label = QLabel("")
        self.conflict_label.setWordWrap(True)
        self.conflict_label.setObjectName("secondaryText")
        editor_layout.addWidget(self.conflict_label)
        editor_layout.addStretch(1)

        self.button_box = QDialogButtonBox(self)
        self.apply_button = self.button_box.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.button_box.accepted.connect(self._accept_if_valid)
        self.button_box.rejected.connect(self.reject)
        root_layout.addWidget(self.button_box)

        self._refresh_binding_list()
        if self._bindings:
            self.binding_list.setCurrentRow(0)
        self._refresh_conflicts()

    def bindings(self) -> tuple[ShortcutBinding, ...]:
        return tuple(self._bindings)

    def _refresh_binding_list(self) -> None:
        current_row = self.binding_list.currentRow()
        self.binding_list.clear()
        for binding in self._bindings:
            current = binding.effective_shortcut or "Unassigned"
            item = QListWidgetItem(f"{binding.section} | {binding.label}\n{current}")
            item.setData(Qt.ItemDataRole.UserRole, binding.id)
            self.binding_list.addItem(item)
        if 0 <= current_row < self.binding_list.count():
            self.binding_list.setCurrentRow(current_row)

    def _load_binding_row(self, row: int) -> None:
        if not (0 <= row < len(self._bindings)):
            self.section_label.setText("No Shortcut Selected")
            self.title_label.setText("")
            self.default_label.setText("")
            self.shortcut_edit.setKeySequence(QKeySequence())
            return
        binding = self._bindings[row]
        self.section_label.setText(binding.section)
        self.title_label.setText(binding.label)
        self.default_label.setText(f"Default: {binding.default_shortcut or 'Unassigned'}")
        self.shortcut_edit.blockSignals(True)
        self.shortcut_edit.setKeySequence(QKeySequence(binding.shortcut or binding.default_shortcut))
        self.shortcut_edit.blockSignals(False)
        self._refresh_conflicts()

    def _handle_shortcut_changed(self, sequence: QKeySequence) -> None:
        row = self.binding_list.currentRow()
        if not (0 <= row < len(self._bindings)):
            return
        portable = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        binding = self._bindings[row]
        normalized = portable.strip()
        if normalized == binding.default_shortcut:
            normalized = ""
        self._bindings[row] = replace(binding, shortcut=normalized)
        self._refresh_binding_list()
        self.binding_list.setCurrentRow(row)
        self._refresh_conflicts()

    def _clear_selected_binding(self) -> None:
        row = self.binding_list.currentRow()
        if not (0 <= row < len(self._bindings)):
            return
        binding = self._bindings[row]
        self._bindings[row] = replace(binding, shortcut="")
        self.shortcut_edit.blockSignals(True)
        self.shortcut_edit.setKeySequence(QKeySequence())
        self.shortcut_edit.blockSignals(False)
        self._refresh_binding_list()
        self.binding_list.setCurrentRow(row)
        self._refresh_conflicts()

    def _reset_selected_binding(self) -> None:
        row = self.binding_list.currentRow()
        if not (0 <= row < len(self._bindings)):
            return
        binding = self._bindings[row]
        self._bindings[row] = replace(binding, shortcut="")
        self._refresh_binding_list()
        self.binding_list.setCurrentRow(row)
        self._load_binding_row(row)
        self._refresh_conflicts()

    def _reset_all_bindings(self) -> None:
        self._bindings = [replace(binding, shortcut="") for binding in self._bindings]
        self._refresh_binding_list()
        if self._bindings:
            self.binding_list.setCurrentRow(0)
        self._refresh_conflicts()

    def _refresh_conflicts(self) -> None:
        conflicts = shortcut_conflicts(self._bindings)
        if not conflicts:
            self.conflict_label.setText("No shortcut conflicts detected.")
            self.apply_button.setEnabled(True)
            return

        current_row = self.binding_list.currentRow()
        current_binding = self._bindings[current_row] if 0 <= current_row < len(self._bindings) else None
        lines: list[str] = []
        for shortcut, binding_ids in conflicts.items():
            if current_binding is not None and current_binding.id not in binding_ids:
                continue
            labels = [binding.label for binding in self._bindings if binding.id in binding_ids]
            lines.append(f"{shortcut}: " + ", ".join(labels))
        self.conflict_label.setText(
            "Conflicts must be resolved before applying.\n" + ("\n".join(lines) if lines else "One or more mappings overlap.")
        )
        self.apply_button.setEnabled(False)

    def _accept_if_valid(self) -> None:
        if not self.apply_button.isEnabled():
            QMessageBox.warning(self, "Shortcut Conflict", "Resolve the conflicting shortcuts before applying them.")
            return
        self.accept()
