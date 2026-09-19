from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from image_triage.ui.nav_rail import NavRail
from image_triage.window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _Recorder:
    """Icon factory that remembers which icons it drew as selected."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, icon_id: str, selected: bool) -> QIcon:
        self.calls.append((icon_id, selected))
        pixmap = QPixmap(4, 4)
        return QIcon(pixmap)


class NavRailTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.rail = NavRail()
        self.rail.add_destination("folders", "Folders", "folder")
        self.rail.add_destination("faces", "Faces", "people")
        self.rail.add_destination("collections", "Collections", "collections")

    def test_the_first_destination_starts_selected(self) -> None:
        self.assertEqual("folders", self.rail.current())
        self.assertTrue(self.rail.button("folders").isChecked())

    def test_destinations_keep_their_order(self) -> None:
        self.assertEqual(("folders", "faces", "collections"), self.rail.keys())

    def test_choosing_a_destination_is_exclusive_and_reported_once(self) -> None:
        seen: list[str] = []
        self.rail.current_changed.connect(seen.append)

        self.rail.set_current("faces")
        self.rail.set_current("faces")

        self.assertEqual(["faces"], seen)
        self.assertTrue(self.rail.button("faces").isChecked())
        self.assertFalse(self.rail.button("folders").isChecked())

    def test_clicking_a_button_selects_it(self) -> None:
        seen: list[str] = []
        self.rail.current_changed.connect(seen.append)

        self.rail.button("collections").click()

        self.assertEqual(["collections"], seen)
        self.assertEqual("collections", self.rail.current())

    def test_unknown_destinations_are_ignored(self) -> None:
        self.rail.set_current("map")
        self.assertEqual("folders", self.rail.current())

    def test_icons_follow_the_selection(self) -> None:
        recorder = _Recorder()
        self.rail.set_icon_factory(recorder)
        recorder.calls.clear()

        self.rail.set_current("faces")

        self.assertIn(("people", True), recorder.calls)
        self.assertIn(("folder", False), recorder.calls)
        self.assertNotIn(("folder", True), recorder.calls)

    def test_buttons_show_their_labels(self) -> None:
        self.assertEqual("Faces", self.rail.button("faces").text())


class _Settings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value


class _LeftPaneHost:
    """Just enough of MainWindow for its page-swapping method."""

    LEFT_NAV_PAGE_KEY = MainWindow.LEFT_NAV_PAGE_KEY
    _show_left_nav_page = MainWindow._show_left_nav_page

    def __init__(self) -> None:
        self._settings = _Settings()
        self.left_nav_pages = QStackedWidget()
        self._left_nav_page_widgets = {key: QWidget() for key in ("folders", "faces", "collections")}
        for page in self._left_nav_page_widgets.values():
            self.left_nav_pages.addWidget(page)
        self.left_nav_rail = NavRail()
        for key, label, glyph, _tip in MainWindow.LEFT_NAV_DESTINATIONS:
            self.left_nav_rail.add_destination(key, label, glyph)


class LeftPaneSwapTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.host = _LeftPaneHost()

    def test_the_rail_offers_folders_faces_and_collections(self) -> None:
        self.assertEqual(
            ("folders", "faces", "collections"),
            tuple(key for key, *_rest in MainWindow.LEFT_NAV_DESTINATIONS),
        )

    def test_a_destination_replaces_the_whole_pane(self) -> None:
        self.host._show_left_nav_page("faces")

        self.assertIs(self.host._left_nav_page_widgets["faces"], self.host.left_nav_pages.currentWidget())
        self.assertEqual("faces", self.host.left_nav_rail.current())

    def test_the_chosen_pane_is_remembered(self) -> None:
        self.host._show_left_nav_page("collections")

        self.assertEqual("collections", self.host._settings.values[MainWindow.LEFT_NAV_PAGE_KEY])

    def test_an_unknown_pane_changes_nothing(self) -> None:
        self.host._show_left_nav_page("map")

        self.assertIs(self.host._left_nav_page_widgets["folders"], self.host.left_nav_pages.currentWidget())
        self.assertEqual({}, self.host._settings.values)


class _ModeTabs:
    def __init__(self, index: int) -> None:
        self.index = index

    def currentIndex(self) -> int:
        return self.index

    def setCurrentIndex(self, index: int) -> None:
        self.index = index


class _ModeHost:
    _set_ui_mode = MainWindow._set_ui_mode

    def __init__(self, index: int) -> None:
        self.mode_tabs = _ModeTabs(index)
        self.handled: list[int] = []

    def _handle_mode_tab_changed(self, index: int) -> None:
        self.handled.append(index)


class ManualOnlyModeTests(unittest.TestCase):
    def test_asking_for_ai_review_stays_in_manual(self) -> None:
        host = _ModeHost(index=0)

        host._set_ui_mode("ai")

        self.assertEqual(0, host.mode_tabs.index)
        self.assertEqual([0], host.handled)

    def test_a_stale_ai_tab_is_pulled_back_to_manual(self) -> None:
        host = _ModeHost(index=1)

        host._set_ui_mode("ai")

        self.assertEqual(0, host.mode_tabs.index)


if __name__ == "__main__":
    unittest.main()
