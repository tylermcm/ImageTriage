"""The toolbar "More" overflow menu must open folded-in menus as popups, not paint them
inside itself (a bare setParent() drops a QMenu's popup flags and stacks it over the
overflow menu's own items)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu


def test_folded_menus_stay_popups(main_window) -> None:
    window = main_window
    menu = QMenu(window)
    window._toolbar.add_topbar_overflow_popup_entries(menu, "AI Results", window._toolbar_menus.build_ai_results_menu)

    owned = menu.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly)
    assert owned, "the overflow menu should own the folded menus"
    embedded = [child.title() for child in owned if not child.isWindow()]
    assert embedded == [], f"menus painted inside the overflow menu instead of popping up: {embedded}"

    labels = [action.text() for action in menu.actions() if action.text()]
    assert labels.count("Show AI Review Summary") == 1
    assert labels.count("AI Review Tag Legend") == 1
    menu.deleteLater()
