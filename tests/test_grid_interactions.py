from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QKeyEvent, QKeySequence, QMouseEvent
from PySide6.QtWidgets import QApplication

from image_triage.grid import ThumbnailGridView
from image_triage.models import ImageRecord
from image_triage.thumbnails import ThumbnailManager
from image_triage.ui.grid_card_renderer import gallery_card_filename_hit_rect


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _key(key: Qt.Key, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers)


def _mouse_move(x: int, y: int) -> QMouseEvent:
    point = QPointF(x, y)
    return QMouseEvent(
        QEvent.Type.MouseMove,
        point,
        point,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _mouse_press(x: int, y: int) -> QMouseEvent:
    point = QPointF(x, y)
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        point,
        point,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


class GridInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def setUp(self) -> None:
        self.grid = ThumbnailGridView(ThumbnailManager())
        self.grid.resize(760, 420)
        self.grid.set_column_count(3)
        self.records = [
            ImageRecord(
                path=f"C:/temp/image_{index:02d}.jpg",
                name=f"image_{index:02d}.jpg",
                size=index + 1,
                modified_ns=index + 1,
            )
            for index in range(18)
        ]
        self.grid.set_items(self.records, request_thumbnails=False)

    def test_empty_space_requests_the_background_context_menu(self) -> None:
        empty_grid = ThumbnailGridView(ThumbnailManager())
        requested = []
        empty_grid.context_menu_requested.connect(
            lambda index, global_pos: requested.append((index, global_pos))
        )
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            QPoint(20, 20),
            QPoint(120, 120),
        )

        empty_grid.contextMenuEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(-1, requested[0][0])

    def test_gallery_only_ties_the_photo_surface_to_the_image(self) -> None:
        self.grid.set_loupe_card_style("gallery")
        tile = self.grid._item_rect(0)
        photo = self.grid._image_rect(tile)
        filename = gallery_card_filename_hit_rect(
            tile,
            self.records[0].name,
            show_actions=True,
        )
        actions = self.grid._winner_button_hit_rect(tile)

        self.assertEqual(0, self.grid._index_at(photo.center().x(), photo.center().y()))
        self.assertEqual(-1, self.grid._index_at(filename.center().x(), filename.center().y()))
        self.assertEqual(-1, self.grid._index_at(actions.center().x(), actions.center().y()))

        footer_y = tile.bottom() - 2
        blank_x = tile.center().x()
        self.assertEqual(-1, self.grid._index_at(blank_x, footer_y))

    def test_gallery_action_button_does_not_change_selection(self) -> None:
        self.grid.set_loupe_card_style("gallery")
        self.grid._set_single_selection(0)
        winners: list[int] = []
        self.grid.winner_requested.connect(winners.append)
        winner = self.grid._winner_button_hit_rect(self.grid._item_rect(1)).center()

        self.grid.mousePressEvent(_mouse_press(winner.x(), winner.y()))

        self.assertEqual([1], winners)
        self.assertEqual(0, self.grid.current_index())
        self.assertEqual([0], self.grid.selected_indexes())

    def tearDown(self) -> None:
        self.grid.deleteLater()

    def test_shift_arrow_extends_selection_from_stable_anchor(self) -> None:
        self.grid.keyPressEvent(_key(Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier))
        self.grid.keyPressEvent(_key(Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier))

        self.assertEqual(self.grid.current_index(), 2)
        self.assertEqual(self.grid.selected_indexes(), [0, 1, 2])
        self.assertEqual(self.grid._selection_anchor, 0)

    def test_plain_arrow_returns_to_single_selection(self) -> None:
        self.grid.keyPressEvent(_key(Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier))
        self.grid.keyPressEvent(_key(Qt.Key.Key_Down))

        self.assertEqual(self.grid.current_index(), 4)
        self.assertEqual(self.grid.selected_indexes(), [4])

    def test_review_shortcuts_follow_live_bindings(self) -> None:
        winners: list[int] = []
        rejects: list[int] = []
        self.grid.winner_requested.connect(winners.append)
        self.grid.reject_requested.connect(rejects.append)
        self.grid.set_review_action_shortcuts(QKeySequence("Ctrl+J"), QKeySequence("R"))

        self.grid.keyPressEvent(_key(Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier))
        self.grid.keyPressEvent(_key(Qt.Key.Key_R))
        self.grid.keyPressEvent(_key(Qt.Key.Key_W))

        self.assertEqual(winners, [0])
        self.assertEqual(rejects, [0])

    def test_action_tooltips_include_current_shortcuts_on_second_line(self) -> None:
        self.grid.set_review_action_shortcuts("Ctrl+J", "Shift+R")

        self.assertEqual(self.grid._action_tooltip("Mark Winner", self.grid._winner_shortcut), "Mark Winner\nShortcut: Ctrl+J")
        self.assertEqual(self.grid._action_tooltip("Reject Selection", self.grid._reject_shortcut), "Reject Selection\nShortcut: Shift+R")

        winner = self.grid._winner_button_hit_rect(self.grid._item_rect(0)).center()
        self.grid.mouseMoveEvent(_mouse_move(winner.x(), winner.y()))
        self.assertEqual(self.grid.viewport().toolTip(), "Mark Winner\nShortcut: Ctrl+J")

    def test_whole_card_hover_repaints_only_previous_and_new_cards(self) -> None:
        updates: list[set[int]] = []
        self.grid._update_selection_tiles = lambda indexes: updates.append(
            {index for index in indexes if index >= 0}
        )
        first = self.grid._item_rect(0).center()
        second = self.grid._item_rect(1).center()

        self.grid.mouseMoveEvent(_mouse_move(first.x(), first.y()))
        self.grid.mouseMoveEvent(_mouse_move(second.x(), second.y()))

        self.assertEqual(self.grid._hovered_index, 1)
        self.assertEqual(updates[0], {0})
        self.assertEqual(updates[1], {0, 1})

    def test_navigation_scrolls_only_enough_to_reveal_active_card(self) -> None:
        self.grid.resize(500, 220)
        self.grid.set_column_count(2)
        QApplication.processEvents()
        for _ in range(6):
            self.grid.keyPressEvent(_key(Qt.Key.Key_Down))

        active = self.grid._content_rect(self.grid.current_index())
        scroll = self.grid.verticalScrollBar().value()
        self.assertGreater(scroll, 0)
        self.assertLessEqual(active.bottom(), scroll + self.grid.viewport().height())
        self.assertGreaterEqual(active.bottom(), scroll)

    def test_filename_tooltip_is_only_returned_when_name_is_elided(self) -> None:
        rect = self.grid._item_rect(0)
        self.assertEqual(self.grid._filename_tooltip(0, rect), "")
        long_name = "this_is_a_deliberately_long_filename_that_cannot_fit_in_the_thumbnail_footer.jpg"
        self.records[0] = ImageRecord(
            path="C:/temp/long.jpg",
            name=long_name,
            size=1,
            modified_ns=1,
        )
        self.grid.set_items(self.records, request_thumbnails=False)

        self.assertEqual(self.grid._filename_tooltip(0, self.grid._item_rect(0)), long_name)


if __name__ == "__main__":
    unittest.main()
