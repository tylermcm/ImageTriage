from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ..library_store import CatalogRoot
from .help_dialog import build_help_button, show_paged_help
from .help_topics import catalog_help_pages


@dataclass(slots=True, frozen=True)
class CatalogSearchDialogResult:
    search_text: str
    root_path: str = ""


class CatalogSearchDialog(QDialog):
    def __init__(self, roots: tuple[CatalogRoot, ...], *, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Browse Global Catalog")
        self.resize(620, 260)
        self._roots = roots

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro_row = QHBoxLayout()
        intro_row.setContentsMargins(0, 0, 0, 0)
        intro_row.setSpacing(8)
        intro = QLabel(
            "The optional catalog indexes filename, path, and bundle snapshots across chosen folders. It helps you search across multiple folders without replacing the normal folder-first workflow."
        )
        intro.setWordWrap(True)
        intro.setObjectName("secondaryText")
        intro_row.addWidget(intro, 1)
        help_button = build_help_button(self, tooltip="Open catalog help")
        help_button.clicked.connect(self._show_help)
        intro_row.addWidget(help_button, 0)
        layout.addLayout(intro_row)

        indexed_roots = len(roots)
        indexed_records = sum(root.indexed_record_count for root in roots)
        summary = QLabel(f"Indexed roots: {indexed_roots} | Indexed image bundles: {indexed_records}")
        summary.setObjectName("secondaryText")
        layout.addWidget(summary)

        form = QFormLayout()
        form.setSpacing(10)
        layout.addLayout(form)

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Search filename or path (leave blank to browse everything indexed)")
        form.addRow("Search", self.search_field)

        self.root_combo = QComboBox()
        self.root_combo.addItem("All Indexed Folders", "")
        for root in roots:
            label = Path(root.path).name or root.path
            suffix = f"{root.indexed_record_count} bundles"
            self.root_combo.addItem(f"{label} ({suffix})", root.path)
        form.addRow("Scope", self.root_combo)

        note = QLabel("AI caches stay folder-local. Catalog search is for discovery and reopening sets, not for forcing a global database workflow.")
        note.setWordWrap(True)
        note.setObjectName("secondaryText")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_data(self) -> CatalogSearchDialogResult:
        return CatalogSearchDialogResult(
            search_text=self.search_field.text().strip(),
            root_path=str(self.root_combo.currentData() or ""),
        )

    def _show_help(self) -> None:
        show_paged_help(
            self,
            title="Global Catalog Help",
            pages=catalog_help_pages(),
        )
