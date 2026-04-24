from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

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


if __name__ == "__main__":
    unittest.main()
