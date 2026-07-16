from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from image_triage.ui.preview_studio import Filmstrip, FilmstripThumb


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FilmstripTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()
        self.strip = Filmstrip(focus="middle")
        self.strip.resize(1000, self.strip.height())
        self.strip.show()
        QApplication.processEvents()

    def tearDown(self) -> None:
        self.strip.close()
        self.strip.deleteLater()
        QApplication.processEvents()

    def _current_thumb(self) -> FilmstripThumb:
        thumbs = [thumb for thumb in self.strip.findChildren(FilmstripThumb) if thumb.isVisible() and thumb._current]
        self.assertEqual(len(thumbs), 1)
        return thumbs[0]

    def assert_current_is_centered(self, current: int, total: int = 100) -> None:
        self.strip.set_source(total, current)
        QApplication.processEvents()

        self.assertEqual(self.strip._count % 2, 1)
        self.assertEqual(self.strip._offset + self.strip._focus_index(), current)
        thumb = self._current_thumb()
        thumb_center = thumb.mapTo(self.strip._reel, thumb.rect().center()).x()
        self.assertAlmostEqual(thumb_center, self.strip._reel.rect().center().x(), delta=2)

    def test_current_frame_stays_centered_including_at_browse_edges(self) -> None:
        for current in (0, 50, 99):
            with self.subTest(current=current):
                self.assert_current_is_centered(current)

    def test_resizing_keeps_an_odd_slot_count_and_recenters_current_frame(self) -> None:
        self.strip.set_source(100, 50)
        for width in (620, 760, 1000, 1240):
            self.strip.resize(width, self.strip.height())
            QApplication.processEvents()
            with self.subTest(width=width):
                self.assertEqual(self.strip._count % 2, 1)
                self.assertEqual(self.strip._offset + self.strip._focus_index(), 50)
                thumb = self._current_thumb()
                thumb_center = thumb.mapTo(self.strip._reel, thumb.rect().center()).x()
                self.assertAlmostEqual(thumb_center, self.strip._reel.rect().center().x(), delta=2)

    def test_scroll_arrows_select_the_next_page_and_keep_it_centered(self) -> None:
        selected: list[int] = []
        self.strip.frame_selected.connect(selected.append)
        self.strip.set_source(100, 50)
        step = max(1, self.strip._count // 2)

        self.strip._scroll(1)
        QApplication.processEvents()

        self.assertEqual(selected, [50 + step])
        self.assertEqual(self.strip._current, 50 + step)
        self.assertEqual(self.strip._offset + self.strip._focus_index(), 50 + step)
        thumb = self._current_thumb()
        thumb_center = thumb.mapTo(self.strip._reel, thumb.rect().center()).x()
        self.assertAlmostEqual(thumb_center, self.strip._reel.rect().center().x(), delta=2)


if __name__ == "__main__":
    unittest.main()
