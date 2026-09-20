"""Clickable folder breadcrumb for the app bar.

Each ancestor is a flat button (click to open that folder); the current folder
is the last, bold segment. When the trail is wider than the space it has, the
leading segments collapse into a "…" button whose menu lists them. Clicking the
current folder asks the host to swap in the editable path box. On Windows the
empty space after the trail is part of the window's drag area.
"""
from __future__ import annotations

from pathlib import PureWindowsPath

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QWidget

CHEVRON_GLYPH = ""
ELLIPSIS_TEXT = "…"


def breadcrumb_segments(path: str) -> list[tuple[str, str]]:
    """Split a folder path into ``(label, full_path)`` pairs, root first."""
    text = (path or "").strip()
    if not text:
        return []
    pure = PureWindowsPath(text)
    parts = list(pure.parts)
    if not parts:
        return []
    segments: list[tuple[str, str]] = []
    anchor = parts[0]
    if pure.drive and anchor.startswith("\\\\"):
        # UNC share: show the share, not the doubled slashes.
        label = pure.drive.strip("\\")
    else:
        label = anchor.rstrip("\\/") or anchor
    current = PureWindowsPath(anchor)
    segments.append((label, str(current)))
    for part in parts[1:]:
        current = current / part
        segments.append((part, str(current)))
    return segments


class BreadcrumbBar(QWidget):
    segment_clicked = Signal(str)
    edit_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appBreadcrumb")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setToolTip("Click a folder to open it, or the current folder to type a path")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._segments: list[tuple[str, str]] = []
        self._plain_label = ""
        self._items: list[QWidget] = []

    # -- content ---------------------------------------------------------

    def set_path(self, path: str) -> None:
        self._segments = breadcrumb_segments(path)
        self._plain_label = ""
        self._rebuild()

    def set_label(self, text: str) -> None:
        """Show a single non-folder scope (a collection, a search) as plain text."""
        self._segments = []
        self._plain_label = text or ""
        self._rebuild()

    def segments(self) -> list[tuple[str, str]]:
        return list(self._segments)

    # -- layout ----------------------------------------------------------

    def sizeHint(self) -> QSize:  # type: ignore[override]
        # The trail adapts to whatever width it is given, so it never asks for
        # its full length; that would feed back into its own resize.
        return QSize(160, 30)

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(60, 30)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._rebuild()

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._items = []

    def _chevron(self) -> QLabel:
        label = QLabel(CHEVRON_GLYPH, self)
        label.setObjectName("breadcrumbChevron")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _segment_button(self, label: str, path: str, *, current: bool) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("breadcrumbCurrent" if current else "breadcrumbSegment")
        button.setText(label)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setToolTip(path)
        if current:
            button.setToolTip("Type a path")
            button.clicked.connect(lambda _checked=False: self.edit_requested.emit())
        else:
            button.clicked.connect(lambda _checked=False, target=path: self.segment_clicked.emit(target))
        return button

    def _ellipsis_button(self, hidden: list[tuple[str, str]]) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("breadcrumbSegment")
        button.setText(ELLIPSIS_TEXT)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        for label, path in hidden:
            action = menu.addAction(label)
            action.setToolTip(path)
            action.triggered.connect(lambda _checked=False, target=path: self.segment_clicked.emit(target))
        button.setMenu(menu)
        return button

    def _row_width(self, widgets: list[QWidget]) -> int:
        spacing = self._layout.spacing()
        return sum(widget.sizeHint().width() for widget in widgets) + spacing * max(0, len(widgets) - 1)

    def _rebuild(self) -> None:
        self._clear()
        if not self._segments:
            if self._plain_label:
                label = QLabel(self._plain_label, self)
                label.setObjectName("breadcrumbCurrentLabel")
                self._layout.addWidget(label)
                self._items.append(label)
            self._layout.addStretch(1)
            return
        last = len(self._segments) - 1
        # Drop leading ancestors into "…" until the trail fits.
        for hidden_count in range(0, last + 1):
            widgets: list[QWidget] = []
            if hidden_count:
                widgets.append(self._ellipsis_button(self._segments[:hidden_count]))
                widgets.append(self._chevron())
            for index in range(hidden_count, last + 1):
                label, path = self._segments[index]
                widgets.append(self._segment_button(label, path, current=index == last))
                if index != last:
                    widgets.append(self._chevron())
            if self._row_width(widgets) <= max(1, self.width()) or hidden_count == last:
                break
            for widget in widgets:
                widget.deleteLater()
        for widget in widgets:
            self._layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self._items = widgets
        self._layout.addStretch(1)

    # -- editing ---------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.edit_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)
