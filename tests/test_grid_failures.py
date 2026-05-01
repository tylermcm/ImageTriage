from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from image_triage.cache import ThumbnailKey
from image_triage.grid import ThumbnailGridView
from image_triage.metadata import CaptureMetadata
from image_triage.models import ImageRecord
from image_triage.thumbnails import ThumbnailManager


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class GridFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def test_failed_thumbnail_paths_are_not_rerequested_until_reload(self) -> None:
        grid = ThumbnailGridView(ThumbnailManager())
        grid.resize(900, 700)
        record = ImageRecord(path="C:/temp/sample.fits", name="sample.fits", size=1, modified_ns=1)
        grid._items = [record]
        grid._visible_item_indexes = [0]
        grid._failed_paths = {record.path}

        with patch.object(grid.thumbnail_manager, "request_thumbnail") as request_thumbnail, patch.object(
            grid.metadata_manager,
            "request_metadata",
        ) as request_metadata:
            grid._request_visible_thumbnails()

        request_thumbnail.assert_not_called()
        self.assertLessEqual(request_metadata.call_count, 1)
        grid.deleteLater()

    def test_offscreen_thumbnail_ready_does_not_create_pixmap_until_visible(self) -> None:
        grid = ThumbnailGridView(ThumbnailManager())
        grid.resize(900, 700)
        records = [
            ImageRecord(
                path=f"C:/temp/sample_{index:03d}.jpg",
                name=f"sample_{index:03d}.jpg",
                size=index + 1,
                modified_ns=index + 1,
            )
            for index in range(30)
        ]
        grid.set_items(records)
        QApplication.processEvents()
        target = grid._thumbnail_target_size()
        offscreen_record = records[-1]
        key = ThumbnailKey(
            path=offscreen_record.path,
            modified_ns=offscreen_record.modified_ns,
            file_size=offscreen_record.size,
            width=target.width(),
            height=target.height(),
        )
        image = QImage(target, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.white)

        grid._handle_thumbnail_ready(key, image)

        self.assertNotIn(key, grid._pixmap_cache)
        grid.deleteLater()

    def test_zoom_source_rect_crops_and_reset_clears_state(self) -> None:
        grid = ThumbnailGridView(ThumbnailManager())
        image = QImage(400, 300, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.white)
        pixmap = QPixmap.fromImage(image)

        source_rect = grid._zoom_source_rect(pixmap, 2.0, (0.5, 0.5))

        self.assertLess(source_rect.width(), pixmap.width())
        self.assertLess(source_rect.height(), pixmap.height())

        changed = grid._apply_image_zoom(0, grid.rect().adjusted(0, 0, 199, 149), pixmap, grid.rect().center(), 2)

        self.assertTrue(changed)
        self.assertEqual(grid._zoom_index, 0)
        self.assertGreater(grid._zoom_factor, 1.0)

        grid._reset_image_zoom()

        self.assertEqual(grid._zoom_index, -1)
        self.assertEqual(grid._zoom_factor, 1.0)
        self.assertEqual(grid._zoom_focus, (0.5, 0.5))
        grid.deleteLater()

    def test_zoom_target_size_requests_higher_resolution_source(self) -> None:
        grid = ThumbnailGridView(ThumbnailManager())
        grid._zoom_index = 0
        grid._zoom_factor = 3.0

        zoom_target = grid._zoom_thumbnail_target_size(QRect(0, 0, 300, 200))

        self.assertGreaterEqual(zoom_target.width(), 900)
        self.assertGreaterEqual(zoom_target.height(), 600)
        grid.deleteLater()

    def test_single_column_cards_fit_viewport_height(self) -> None:
        grid = ThumbnailGridView(ThumbnailManager())
        grid.resize(1600, 900)
        grid.show()
        grid.set_column_count(1)
        records = [
            ImageRecord(
                path=f"C:/temp/single_{index}.jpg",
                name=f"single_{index}.jpg",
                size=1,
                modified_ns=index + 1,
            )
            for index in range(2)
        ]
        for record in records:
            grid.metadata_manager._cache[grid.metadata_manager.make_key(record)] = CaptureMetadata(
                path=record.path,
                width=1200,
                height=1800,
            )
        grid.set_items(records)
        QApplication.processEvents()

        self.assertEqual(grid._tile_height(), grid.viewport().height() - (grid._margin * 2))
        self.assertEqual(grid._tile_width(), grid.viewport().width() - (grid._margin * 2))
        grid.deleteLater()

    def test_single_column_image_draw_rect_is_top_aligned(self) -> None:
        grid = ThumbnailGridView(ThumbnailManager())
        grid.resize(1600, 900)
        grid.set_column_count(1)
        image_rect = QRect(10, 10, 1000, 700)
        pixmap = QPixmap.fromImage(QImage(600, 400, QImage.Format.Format_ARGB32))

        draw_rect = grid._image_draw_rect(image_rect, pixmap)

        self.assertEqual(draw_rect.top(), image_rect.top())
        self.assertGreaterEqual(draw_rect.left(), image_rect.left())
        self.assertLessEqual(draw_rect.right(), image_rect.right())
        grid.deleteLater()


if __name__ == "__main__":
    unittest.main()
