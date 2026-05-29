from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .shell_actions import open_with_default


@dataclass
class CategoryDraft:
    slug: str
    enabled: bool = True
    prompts: list[str] = field(default_factory=list)


def _read_categories_csv(path: Path) -> list[CategoryDraft]:
    if not path.exists():
        return []
    drafts_by_slug: dict[str, CategoryDraft] = {}
    order: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            slug = (row.get("category") or "").strip()
            prompt = (row.get("prompt") or "").strip()
            if not slug:
                continue
            enabled_raw = (row.get("enabled") or "1").strip()
            enabled = enabled_raw not in {"0", "false", "False", "no", "off"}
            draft = drafts_by_slug.get(slug)
            if draft is None:
                draft = CategoryDraft(slug=slug, enabled=enabled, prompts=[])
                drafts_by_slug[slug] = draft
                order.append(slug)
            else:
                # If any row for this category is enabled, treat the whole
                # category as enabled (the editor is per-category).
                draft.enabled = draft.enabled or enabled
            if prompt:
                draft.prompts.append(prompt)
    return [drafts_by_slug[slug] for slug in order]


def _write_categories_csv(path: Path, drafts: list[CategoryDraft]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["category", "prompt", "enabled"])
        for draft in drafts:
            enabled_text = "1" if draft.enabled else "0"
            cleaned = [p.strip() for p in draft.prompts if p and p.strip()]
            if not cleaned:
                writer.writerow([draft.slug, "", enabled_text])
                continue
            for prompt in cleaned:
                writer.writerow([draft.slug, prompt, enabled_text])


class _PromptRowWidget(QWidget):
    def __init__(self, text: str, on_remove, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(6)
        self.edit = QPlainTextEdit()
        self.edit.setObjectName("categoryPromptInput")
        self.edit.setPlainText(text)
        self.edit.setTabChangesFocus(True)
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Fix the editor to exactly 4 visible text lines (computed from the
        # widget's own font metrics so it scales with the user's font size).
        fm = self.edit.fontMetrics()
        target_height = fm.lineSpacing() * 4 + 16  # +16 covers 6px top/bottom padding + frame
        self.edit.setFixedHeight(target_height)
        self.edit.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        self.edit.setStyleSheet(
            "QPlainTextEdit#categoryPromptInput {"
            " padding: 6px 8px;"
            "}"
        )
        layout.addWidget(self.edit, 1)
        remove_button = QPushButton("×")
        remove_button.setObjectName("categoryPromptRemove")
        remove_button.setFixedWidth(28)
        remove_button.setFixedHeight(28)
        remove_button.setToolTip("Remove this prompt")
        remove_button.clicked.connect(lambda: on_remove(self))
        # Pin the remove button to the top of the row so it stays in place when
        # the prompt grows to multiple lines.
        layout.addWidget(remove_button, 0, Qt.AlignmentFlag.AlignTop)

    def text(self) -> str:
        return self.edit.toPlainText().strip()

    def set_focus_to_edit(self) -> None:
        self.edit.setFocus()


class CategoryPromptsDialog(QDialog):
    def __init__(self, csv_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._csv_path = Path(csv_path)
        self.setWindowTitle("Edit Category Prompts")
        self.setObjectName("categoryPromptsDialog")
        self.setModal(True)
        self.resize(760, 520)
        self.setMinimumSize(680, 420)

        self._drafts: list[CategoryDraft] = _read_categories_csv(self._csv_path)
        self._current_index: int = -1
        self._prompt_widgets: list[_PromptRowWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # --- Left: category list ---
        left = QFrame()
        left.setObjectName("categorySidebar")
        left.setStyleSheet(
            "QFrame#categorySidebar {"
            " background: #161c25;"
            " border-right: 1px solid rgba(255,255,255,0.05);"
            "}"
        )
        left.setFixedWidth(220)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)

        header = QLabel("Categories")
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header.setFont(header_font)
        left_layout.addWidget(header)

        self._list = QListWidget()
        self._list.setObjectName("categoryList")
        self._list.setStyleSheet(
            "QListWidget#categoryList { background: transparent; border: none; font-size: 12px; }"
            "QListWidget#categoryList::item { padding: 8px 10px; margin: 2px 0; border-radius: 5px; color: #c4cbd6; }"
            "QListWidget#categoryList::item:selected { background: rgba(47, 111, 214, 0.25); color: white; }"
            "QListWidget#categoryList::item:hover { background: rgba(255,255,255,0.04); }"
        )
        self._list.currentRowChanged.connect(self._handle_select)
        left_layout.addWidget(self._list, 1)

        list_buttons = QHBoxLayout()
        list_buttons.setContentsMargins(0, 0, 0, 0)
        list_buttons.setSpacing(6)
        add_button = QPushButton("+ Category")
        add_button.setObjectName("categoryAddBtn")
        add_button.clicked.connect(self._handle_add_category)
        list_buttons.addWidget(add_button, 1)
        remove_category_button = QPushButton("− Remove")
        remove_category_button.setObjectName("categoryRemoveBtn")
        remove_category_button.setToolTip("Remove the selected category")
        remove_category_button.clicked.connect(self._handle_remove_category)
        list_buttons.addWidget(remove_category_button, 0)
        left_layout.addLayout(list_buttons)
        body.addWidget(left, 0)

        # --- Right: editor stack ---
        self._stack = QStackedWidget()
        empty_page = QWidget()
        empty_layout = QVBoxLayout(empty_page)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label = QLabel("Select a category to edit its prompts,\nor click + Category to add one.")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.setStyleSheet("color: #8d99ac; font-size: 12px;")
        empty_layout.addWidget(empty_label)
        self._stack.addWidget(empty_page)

        editor_page = QWidget()
        editor_layout = QVBoxLayout(editor_page)
        editor_layout.setContentsMargins(20, 18, 20, 18)
        editor_layout.setSpacing(12)

        slug_row = QHBoxLayout()
        slug_row.setSpacing(8)
        slug_label = QLabel("Slug:")
        slug_label.setMinimumWidth(60)
        slug_row.addWidget(slug_label)
        self._slug_input = QLineEdit()
        self._slug_input.setObjectName("categorySlugInput")
        self._slug_input.setPlaceholderText("e.g. wildlife")
        self._slug_input.editingFinished.connect(self._handle_slug_changed)
        slug_row.addWidget(self._slug_input, 1)
        self._enabled_check = QCheckBox("Enabled")
        self._enabled_check.toggled.connect(self._handle_enabled_changed)
        slug_row.addWidget(self._enabled_check, 0)
        editor_layout.addLayout(slug_row)

        helper = QLabel(
            "Each prompt is a CLIP-style natural-language description. Images are"
            " assigned to the category whose strongest prompt embedding matches."
        )
        helper.setWordWrap(True)
        helper.setStyleSheet("color: #8d99ac; font-size: 11px;")
        editor_layout.addWidget(helper)

        prompts_header = QHBoxLayout()
        prompts_label = QLabel("Prompts")
        prompts_label.setStyleSheet("font-weight: 600;")
        prompts_header.addWidget(prompts_label, 1)
        add_prompt_button = QPushButton("+ Add prompt")
        add_prompt_button.setObjectName("categoryAddPromptBtn")
        add_prompt_button.clicked.connect(self._handle_add_prompt)
        prompts_header.addWidget(add_prompt_button, 0)
        editor_layout.addLayout(prompts_header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._prompts_host = QWidget()
        self._prompts_layout = QVBoxLayout(self._prompts_host)
        self._prompts_layout.setContentsMargins(0, 0, 0, 0)
        self._prompts_layout.setSpacing(2)
        self._prompts_layout.addStretch(1)
        self._prompts_host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
        scroll.setWidget(self._prompts_host)
        editor_layout.addWidget(scroll, 1)

        self._stack.addWidget(editor_page)
        body.addWidget(self._stack, 1)

        root.addLayout(body, 1)

        # --- Footer ---
        footer = QHBoxLayout()
        footer.setContentsMargins(14, 10, 14, 10)
        footer.setSpacing(8)
        open_external_button = QPushButton("Open in External Editor")
        open_external_button.setObjectName("categoryOpenExternalBtn")
        open_external_button.setToolTip("Open the raw CSV file in your system's default editor")
        open_external_button.clicked.connect(self._open_external)
        footer.addWidget(open_external_button, 0)
        footer.addStretch(1)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self._handle_save)
        button_box.rejected.connect(self.reject)
        footer.addWidget(button_box, 0)
        root.addLayout(footer, 0)

        self._reload_list(select_index=0 if self._drafts else -1)

    # ---------- list + editor wiring ----------

    def _reload_list(self, *, select_index: int) -> None:
        previous_block = self._list.blockSignals(True)
        self._list.clear()
        for draft in self._drafts:
            item = QListWidgetItem(draft.slug or "(unnamed)")
            item.setData(Qt.ItemDataRole.UserRole, draft.slug)
            if not draft.enabled:
                item.setForeground(Qt.GlobalColor.gray)
            self._list.addItem(item)
        self._list.blockSignals(previous_block)
        if 0 <= select_index < len(self._drafts):
            self._list.setCurrentRow(select_index)
        else:
            self._current_index = -1
            self._stack.setCurrentIndex(0)

    def _handle_select(self, row: int) -> None:
        if not 0 <= row < len(self._drafts):
            self._current_index = -1
            self._stack.setCurrentIndex(0)
            return
        self._current_index = row
        draft = self._drafts[row]
        previous = self._slug_input.blockSignals(True)
        self._slug_input.setText(draft.slug)
        self._slug_input.blockSignals(previous)
        previous = self._enabled_check.blockSignals(True)
        self._enabled_check.setChecked(draft.enabled)
        self._enabled_check.blockSignals(previous)
        self._rebuild_prompt_widgets(draft.prompts)
        self._stack.setCurrentIndex(1)

    def _rebuild_prompt_widgets(self, prompts: list[str]) -> None:
        for widget in self._prompt_widgets:
            widget.deleteLater()
        self._prompt_widgets.clear()
        # Stretch is the last item in _prompts_layout — keep it pinned to the bottom.
        for prompt in prompts:
            self._append_prompt_widget(prompt)
        if not prompts:
            self._append_prompt_widget("")

    def _append_prompt_widget(self, text: str) -> _PromptRowWidget:
        row = _PromptRowWidget(text, self._handle_remove_prompt, parent=self._prompts_host)
        insert_at = max(0, self._prompts_layout.count() - 1)
        self._prompts_layout.insertWidget(insert_at, row)
        self._prompt_widgets.append(row)
        return row

    # ---------- editor callbacks ----------

    def _handle_slug_changed(self) -> None:
        if not 0 <= self._current_index < len(self._drafts):
            return
        new_slug = self._slug_input.text().strip()
        if not new_slug:
            QMessageBox.warning(self, "Edit Category Prompts", "Slug cannot be empty.")
            self._slug_input.setText(self._drafts[self._current_index].slug)
            return
        for index, draft in enumerate(self._drafts):
            if index != self._current_index and draft.slug == new_slug:
                QMessageBox.warning(self, "Edit Category Prompts", f"Another category already uses the slug '{new_slug}'.")
                self._slug_input.setText(self._drafts[self._current_index].slug)
                return
        self._drafts[self._current_index].slug = new_slug
        item = self._list.item(self._current_index)
        if item is not None:
            item.setText(new_slug)
            item.setData(Qt.ItemDataRole.UserRole, new_slug)

    def _handle_enabled_changed(self, checked: bool) -> None:
        if not 0 <= self._current_index < len(self._drafts):
            return
        self._drafts[self._current_index].enabled = bool(checked)
        item = self._list.item(self._current_index)
        if item is not None:
            item.setForeground(Qt.GlobalColor.gray if not checked else Qt.GlobalColor.white)

    def _handle_add_prompt(self) -> None:
        if not 0 <= self._current_index < len(self._drafts):
            return
        row = self._append_prompt_widget("")
        row.edit.setFocus()

    def _handle_remove_prompt(self, widget: _PromptRowWidget) -> None:
        if widget in self._prompt_widgets:
            self._prompt_widgets.remove(widget)
        widget.deleteLater()
        if not self._prompt_widgets:
            self._append_prompt_widget("")

    def _handle_add_category(self) -> None:
        base_slug = "new_category"
        existing = {draft.slug for draft in self._drafts}
        slug = base_slug
        counter = 1
        while slug in existing:
            counter += 1
            slug = f"{base_slug}_{counter}"
        self._commit_current_prompts_to_draft()
        self._drafts.append(CategoryDraft(slug=slug, enabled=True, prompts=[""]))
        self._reload_list(select_index=len(self._drafts) - 1)
        self._slug_input.setFocus()
        self._slug_input.selectAll()

    def _handle_remove_category(self) -> None:
        if not 0 <= self._current_index < len(self._drafts):
            return
        draft = self._drafts[self._current_index]
        result = QMessageBox.question(
            self,
            "Edit Category Prompts",
            f"Remove category '{draft.slug}' and all its prompts?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        del self._drafts[self._current_index]
        next_index = min(self._current_index, len(self._drafts) - 1)
        self._reload_list(select_index=next_index)

    def _commit_current_prompts_to_draft(self) -> None:
        if not 0 <= self._current_index < len(self._drafts):
            return
        prompts = [w.text() for w in self._prompt_widgets if w.text()]
        self._drafts[self._current_index].prompts = prompts

    # ---------- footer ----------

    def _open_external(self) -> None:
        if not self._csv_path.exists():
            try:
                self._csv_path.parent.mkdir(parents=True, exist_ok=True)
                self._csv_path.write_text("category,prompt,enabled\n", encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Edit Category Prompts", f"Could not create the file.\n\n{exc}")
                return
        open_with_default(str(self._csv_path))

    def _handle_save(self) -> None:
        self._commit_current_prompts_to_draft()
        cleaned: list[CategoryDraft] = []
        seen_slugs: set[str] = set()
        for draft in self._drafts:
            slug = draft.slug.strip()
            if not slug:
                QMessageBox.warning(self, "Edit Category Prompts", "Every category needs a non-empty slug.")
                return
            if slug in seen_slugs:
                QMessageBox.warning(self, "Edit Category Prompts", f"Duplicate slug '{slug}' — slugs must be unique.")
                return
            seen_slugs.add(slug)
            prompts = [p.strip() for p in draft.prompts if p and p.strip()]
            cleaned.append(CategoryDraft(slug=slug, enabled=draft.enabled, prompts=prompts))
        try:
            _write_categories_csv(self._csv_path, cleaned)
        except OSError as exc:
            QMessageBox.warning(self, "Edit Category Prompts", f"Could not save the file.\n\n{exc}")
            return
        self.accept()
