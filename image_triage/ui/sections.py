"""Collapsible section headers for the left navigation pane.

A header is a chevron, an optional glyph, a title, and an optional trailing
control. Clicking anywhere on it toggles the section, which is what makes a
sidebar of several lists usable in a pane that is only part of the panel height.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QIcon, QPainter, QPalette, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


class _Chevron(QWidget):
    """A painted disclosure triangle.

    Drawn rather than set as text: the arrow characters are missing from the
    UI font and render as tofu, the same trap the toolbar glyphs hit.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navSectionChevron")
        self.setFixedSize(12, 12)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._expanded = True

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = self.palette().color(QPalette.ColorRole.WindowText)
        colour.setAlpha(205)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(colour))
        w, h = self.width(), self.height()
        if self._expanded:
            points = [QPointF(w * 0.12, h * 0.36), QPointF(w * 0.88, h * 0.36), QPointF(w * 0.5, h * 0.76)]
        else:
            points = [QPointF(w * 0.36, h * 0.12), QPointF(w * 0.76, h * 0.5), QPointF(w * 0.36, h * 0.88)]
        painter.drawPolygon(QPolygonF(points))
        painter.end()


class SectionHeader(QWidget):
    """A clickable section title that reports its expanded state."""

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        *,
        icon: QIcon | None = None,
        trailing: QWidget | None = None,
        expanded: bool = True,
        collapsible: bool = True,
        chevron_on_right: bool = False,
        icon_size: int = 14,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("navSectionHeader")
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if collapsible else Qt.CursorShape.ArrowCursor
        )
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._expanded = expanded
        self._collapsible = collapsible
        self._icon_size = icon_size

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(6)

        self.chevron = _Chevron(self)
        self.chevron.setVisible(collapsible)
        if not chevron_on_right:
            layout.addWidget(self.chevron, 0)

        self.glyph = QLabel(self)
        self.glyph.setObjectName("navSectionGlyph")
        self.glyph.setFixedSize(icon_size, icon_size)
        self.glyph.setVisible(icon is not None)
        if icon is not None:
            self.glyph.setPixmap(icon.pixmap(icon_size, icon_size))
        layout.addWidget(self.glyph, 0)

        self.title = QLabel(title, self)
        self.title.setObjectName("navSectionTitle")
        layout.addWidget(self.title, 1)

        if trailing is not None:
            trailing.setParent(self)
            layout.addWidget(trailing, 0)
        if chevron_on_right:
            layout.addWidget(self.chevron, 0)
        self._trailing = trailing

        self._sync()

    def set_icon(self, icon: QIcon | None) -> None:
        self.glyph.setVisible(icon is not None)
        if icon is not None:
            self.glyph.setPixmap(icon.pixmap(self._icon_size, self._icon_size))

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._sync()
        self.toggled.emit(expanded)

    def _sync(self) -> None:
        self.chevron.set_expanded(self._expanded)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        # The trailing control keeps its own clicks (e.g. "new project").
        if self._trailing is not None and self._trailing.geometry().contains(
            event.position().toPoint()
        ):
            super().mousePressEvent(event)
            return
        if self._collapsible and event.button() == Qt.MouseButton.LeftButton:
            self.set_expanded(not self._expanded)
            event.accept()
            return
        super().mousePressEvent(event)
