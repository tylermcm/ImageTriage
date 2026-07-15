from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from image_triage.grid import ThumbnailGridView
from image_triage.thumbnails import ThumbnailManager


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class GridCardStyleTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()
        self.grid = ThumbnailGridView(ThumbnailManager())
        self.grid.resize(1200, 800)
        self.grid.set_loupe_card_style("detailed")

    def tearDown(self) -> None:
        self.grid.deleteLater()

    def test_detailed_style_uses_detailed_renderer_for_columns_one_through_eight(self) -> None:
        for columns in range(1, 9):
            self.grid.set_column_count(columns)
            with self.subTest(columns=columns):
                self.assertTrue(self.grid._use_new_grid_card())
                self.assertFalse(self.grid._use_loupe_card_style())
                self.assertFalse(self.grid._use_compact_grid_card())

    def test_single_column_detailed_card_fits_and_centers_in_viewport(self) -> None:
        self.grid.set_column_count(1)

        available_height = self.grid.viewport().height() - (self.grid._margin * 2)
        inner_width = self.grid.viewport().width() - (self.grid._margin * 2)
        self.assertLessEqual(self.grid._tile_height(), available_height)
        self.assertEqual(
            self.grid._row_x_offset,
            max(0, (inner_width - self.grid._tile_width()) // 2),
        )


if __name__ == "__main__":
    unittest.main()
