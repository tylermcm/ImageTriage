from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout


class HelpMarkdownDialog(QDialog):
    def __init__(self, *, title: str, markdown: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(760, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.browser = QTextBrowser(self)
        self.browser.setObjectName("helpMarkdownView")
        self.browser.setOpenExternalLinks(True)
        self.browser.setOpenLinks(True)
        self.browser.setUndoRedoEnabled(False)
        self.browser.setReadOnly(True)
        self.browser.setMarkdown(markdown.strip())
        self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        layout.addWidget(self.browser, 1)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, Qt.Orientation.Horizontal, self)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)
