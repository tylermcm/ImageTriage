"""The left navigation rail.

A vertical column of labelled destinations beside the library panel. Choosing
one swaps the whole pane beside it (Folders, Faces, Collections, ...) instead of
splitting one pane between several lists, so each list gets the full height.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QButtonGroup, QSizePolicy, QToolButton, QVBoxLayout, QWidget

# (icon id, selected) -> icon. Supplied by the window so icons follow its theme.
IconFactory = Callable[[str, bool], QIcon]

RAIL_WIDTH_PX = 76
BUTTON_WIDTH_PX = 64
BUTTON_MIN_HEIGHT_PX = 58
ICON_PX = 22


class NavRail(QWidget):
    """Exclusive, labelled destination buttons that report the chosen key."""

    current_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("leftNavRail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(RAIL_WIDTH_PX)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 10, 6, 10)
        self._layout.setSpacing(4)
        self._layout.addStretch(1)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QToolButton] = {}
        self._icon_ids: dict[str, str] = {}
        self._icon_factory: IconFactory | None = None
        self._current = ""

    def add_destination(self, key: str, label: str, icon_id: str, *, tooltip: str = "") -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("leftNavButton")
        button.setProperty("navKey", key)
        button.setText(label)
        button.setToolTip(tooltip or label)
        button.setAccessibleName(label)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIconSize(QSize(ICON_PX, ICON_PX))
        button.setFixedWidth(BUTTON_WIDTH_PX)
        button.setMinimumHeight(BUTTON_MIN_HEIGHT_PX)
        button.clicked.connect(lambda _checked=False, target=key: self.set_current(target))
        self._group.addButton(button)
        self._buttons[key] = button
        self._icon_ids[key] = icon_id
        # Destinations stack from the top, ahead of any added sections.
        self._layout.insertWidget(len(self._buttons) - 1, button, 0, Qt.AlignmentFlag.AlignHCenter)
        if not self._current:
            self.set_current(key, emit=False)
        else:
            self._refresh_icon(key)
        return button

    def add_section(self, widget: QWidget) -> None:
        """Add a block (such as pinned tools) below the destinations."""
        self._layout.insertWidget(self._stretch_index(), widget, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_footer(self, widget: QWidget) -> None:
        """Pin a widget to the bottom of the rail."""
        self._layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)

    def _stretch_index(self) -> int:
        for index in range(self._layout.count()):
            if self._layout.itemAt(index).spacerItem() is not None:
                return index
        return self._layout.count()

    def set_icon_factory(self, factory: IconFactory) -> None:
        self._icon_factory = factory
        self.refresh_icons()

    def refresh_icons(self) -> None:
        for key in self._buttons:
            self._refresh_icon(key)

    def keys(self) -> tuple[str, ...]:
        return tuple(self._buttons)

    def button(self, key: str) -> QToolButton | None:
        return self._buttons.get(key)

    def current(self) -> str:
        return self._current

    def set_current(self, key: str, *, emit: bool = True) -> None:
        button = self._buttons.get(key)
        if button is None:
            return
        changed = key != self._current
        self._current = key
        button.setChecked(True)
        self.refresh_icons()
        if changed and emit:
            self.current_changed.emit(key)

    def _refresh_icon(self, key: str) -> None:
        if self._icon_factory is None:
            return
        button = self._buttons[key]
        button.setIcon(self._icon_factory(self._icon_ids[key], key == self._current))
