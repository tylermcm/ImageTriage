from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from image_triage.ui.grid_card_renderer import (
    GridCardData,
    grid_card_action_rects,
    render_grid_card_pixmap,
)


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _luminance(color: QColor) -> float:
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


class GridCardRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()

    def test_bottom_scrim_keeps_top_bright_and_bottom_dark(self) -> None:
        source = QPixmap(QSize(1200, 800))
        source.fill(QColor(230, 230, 230))

        card = render_grid_card_pixmap(
            QSize(560, 330),
            source,
            GridCardData(duplicate_visible=False, ai_visible=False),
        ).toImage()

        image_top = 7
        image_height = 316
        image_bottom = image_top + image_height - 1
        x = card.width() // 2

        upper = _luminance(card.pixelColor(x, image_top + round(image_height * 0.18)))
        mid = _luminance(card.pixelColor(x, image_top + round(image_height * 0.65)))
        lower = _luminance(card.pixelColor(x, image_bottom - 4))

        self.assertGreater(upper, 205)
        self.assertGreater(mid, lower)
        self.assertLess(lower, 50)

    def test_compact_card_renders_at_small_sizes(self) -> None:
        source = QPixmap(QSize(1200, 800))
        source.fill(QColor(120, 130, 140))

        for size in (QSize(300, 218), QSize(180, 131), QSize(120, 87)):
            card = render_grid_card_pixmap(
                QSize(size),
                source,
                GridCardData(selected=True, favorite=True),
                compact=True,
            )
            self.assertFalse(card.isNull())
            self.assertEqual(card.size(), size)

    def test_action_rects_stay_inside_card_for_both_layouts(self) -> None:
        for width, height, compact in ((560, 407, False), (385, 280, False), (300, 218, True), (180, 131, True)):
            rect = QRect(0, 0, width, height)
            hits = grid_card_action_rects(rect, compact=compact)
            for name, button in (("favorite", hits.favorite), ("reject", hits.reject)):
                with self.subTest(width=width, compact=compact, button=name):
                    self.assertTrue(button.isValid())
                    self.assertTrue(rect.contains(button), f"{button} outside {rect}")
        self.assertLess(
            grid_card_action_rects(QRect(0, 0, 300, 218), compact=True).favorite.right(),
            grid_card_action_rects(QRect(0, 0, 300, 218), compact=True).reject.left(),
        )


if __name__ == "__main__":
    unittest.main()
