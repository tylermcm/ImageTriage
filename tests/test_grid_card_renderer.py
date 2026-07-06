from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from image_triage.ui.grid_card_renderer import GridCardData, render_grid_card_pixmap


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


if __name__ == "__main__":
    unittest.main()
