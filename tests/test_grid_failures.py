from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from image_triage.cache import ThumbnailKey
from image_triage.grid import ThumbnailGridView
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


if __name__ == "__main__":
    unittest.main()
