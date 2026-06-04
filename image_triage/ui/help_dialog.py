from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextBlockFormat, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
)


@dataclass(slots=True, frozen=True)
class HelpPage:
    title: str
    markdown: str


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
        self.browser.document().setDefaultStyleSheet(_HELP_DOCUMENT_CSS)
        self.browser.setMarkdown(dedent(markdown).strip())
        _apply_help_document_spacing(self.browser)
        self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        layout.addWidget(self.browser, 1)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, Qt.Orientation.Horizontal, self)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)


class PagedHelpDialog(QDialog):
    def __init__(self, *, title: str, pages: tuple[HelpPage, ...], parent=None) -> None:
        super().__init__(parent)
        self._pages = pages or (HelpPage("Help", "No help content is available yet."),)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(860, 700)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        self.page_list = QListWidget(self)
        self.page_list.setObjectName("helpPageList")
        self.page_list.setMaximumWidth(230)
        self.page_list.setMinimumWidth(190)
        self.page_list.setStyleSheet("font-size: 13px;")
        for page in self._pages:
            self.page_list.addItem(QListWidgetItem(page.title))
        self.page_list.currentRowChanged.connect(self._set_page)
        body.addWidget(self.page_list, 0)

        content = QVBoxLayout()
        content.setSpacing(8)
        body.addLayout(content, 1)

        self.browser = QTextBrowser(self)
        self.browser.setObjectName("helpMarkdownView")
        self.browser.setStyleSheet("font-size: 14px;")
        self.browser.setOpenExternalLinks(True)
        self.browser.setOpenLinks(True)
        self.browser.setUndoRedoEnabled(False)
        self.browser.setReadOnly(True)
        self.browser.document().setDefaultStyleSheet(_HELP_DOCUMENT_CSS)
        content.addWidget(self.browser, 1)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)
        self.page_counter = QLabel(self)
        self.page_counter.setObjectName("secondaryText")
        nav_row.addWidget(self.page_counter, 1)
        self.previous_button = QPushButton("Previous", self)
        self.next_button = QPushButton("Next", self)
        self.ok_button = QPushButton("OK", self)
        self.previous_button.clicked.connect(self._previous_page)
        self.next_button.clicked.connect(self._next_page)
        self.ok_button.clicked.connect(self.accept)
        nav_row.addWidget(self.previous_button)
        nav_row.addWidget(self.next_button)
        nav_row.addWidget(self.ok_button)
        root.addLayout(nav_row)

        self.page_list.setCurrentRow(0)

    def _set_page(self, index: int) -> None:
        index = max(0, min(index, len(self._pages) - 1))
        page = self._pages[index]
        self.browser.setMarkdown(dedent(page.markdown).strip())
        _apply_help_document_spacing(self.browser)
        self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        self.page_counter.setText(f"Page {index + 1} of {len(self._pages)}")
        self.previous_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < len(self._pages) - 1)

    def _previous_page(self) -> None:
        self.page_list.setCurrentRow(max(0, self.page_list.currentRow() - 1))

    def _next_page(self) -> None:
        self.page_list.setCurrentRow(min(len(self._pages) - 1, self.page_list.currentRow() + 1))


def build_help_button(parent=None, *, tooltip: str = "Open help") -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("contextHelpButton")
    button.setText("?")
    button.setToolTip(tooltip)
    button.setAutoRaise(False)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setFixedSize(26, 26)
    return button


def show_paged_help(parent, *, title: str, pages: tuple[HelpPage, ...]) -> int:
    dialog = PagedHelpDialog(title=title, pages=pages, parent=parent)
    return int(dialog.exec())


def _apply_help_document_spacing(browser: QTextBrowser) -> None:
    document = browser.document()
    option = document.defaultTextOption()
    option.setWrapMode(QTextOption.WrapMode.WordWrap)
    document.setDefaultTextOption(option)
    proportional_line_height = int(QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)

    block = document.begin()
    cursor = QTextCursor(document)
    while block.isValid():
        cursor.setPosition(block.position())
        block_format = QTextBlockFormat(block.blockFormat())
        heading_level = 0
        if hasattr(block_format, "headingLevel"):
            try:
                heading_level = int(block_format.headingLevel())
            except (TypeError, ValueError):
                heading_level = 0
        if heading_level == 1:
            block_format.setTopMargin(0)
            block_format.setBottomMargin(12)
            block_format.setLineHeight(118.0, proportional_line_height)
        elif heading_level >= 2:
            block_format.setTopMargin(12)
            block_format.setBottomMargin(8)
            block_format.setLineHeight(122.0, proportional_line_height)
        else:
            block_format.setTopMargin(0)
            block_format.setBottomMargin(11)
            block_format.setLineHeight(150.0, proportional_line_height)
        cursor.setBlockFormat(block_format)
        block = block.next()


_HELP_DOCUMENT_CSS = """
body {
    line-height: 1.45;
}
h1 {
    margin-top: 0;
    margin-bottom: 14px;
    line-height: 1.18;
}
h2 {
    margin-top: 18px;
    margin-bottom: 8px;
    line-height: 1.2;
}
p {
    margin-top: 0;
    margin-bottom: 12px;
}
ul, ol {
    margin-top: 4px;
    margin-bottom: 14px;
}
li {
    margin-bottom: 5px;
}
"""
