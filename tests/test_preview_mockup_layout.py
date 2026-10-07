"""Live popout controls that the approved visual mockup depends on."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from image_triage.app_identity import user_settings
from image_triage.models import ImageRecord
from image_triage.preview import FullScreenPreview, PreviewEntry


class PreviewMockupLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        settings = user_settings()
        self._saved_preferences = {
            key: settings.value(key)
            for key in (
                FullScreenPreview.FILMSTRIP_COLLAPSED_KEY,
                FullScreenPreview.FILMSTRIP_THUMB_HEIGHT_KEY,
                FullScreenPreview.FILMSTRIP_THUMB_RATIO_KEY,
            )
        }
        settings.remove(FullScreenPreview.FILMSTRIP_THUMB_HEIGHT_KEY)
        settings.remove(FullScreenPreview.FILMSTRIP_THUMB_RATIO_KEY)
        settings.remove(FullScreenPreview.FILMSTRIP_COLLAPSED_KEY)
        self.preview = FullScreenPreview()
        self.preview.resize(1440, 900)
        self.preview.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.preview.close()
        settings = user_settings()
        for key, value in self._saved_preferences.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        self.app.processEvents()

    def test_rating_buttons_emit_real_record_path_and_refresh_from_annotation(self) -> None:
        record = ImageRecord(path=r"C:\photos\frame.jpg", name="frame.jpg", size=100, modified_ns=1)
        self.preview._source_entries = [PreviewEntry(record, record.path, rating=3)]
        self.preview._rebuild_entries()
        self.preview._update_info_label()
        emitted: list[tuple[str, int]] = []
        self.preview.rating_requested.connect(lambda path, rating: emitted.append((path, rating)))

        self.preview._mockup_star_buttons[4].click()
        self.assertEqual(emitted, [(record.path, 5)])
        self.preview.set_annotation_state(record.path, winner=False, reject=False, rating=5)
        self.assertEqual(self.preview._entries[0].rating, 5)
        self.assertIn("#f2c858", self.preview._mockup_star_buttons[4].styleSheet())
        self.preview._mockup_star_buttons[4].click()
        self.assertEqual(emitted[-1], (record.path, 0))


if __name__ == "__main__":
    unittest.main()
