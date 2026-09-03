from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from image_triage.ui.busy_overlay import BusyOverlay


class BusyOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_message_shows_and_clears_in_place(self) -> None:
        host = QWidget()
        host.resize(640, 480)
        overlay = BusyOverlay(host)
        overlay.attach_to(host)
        host.show()

        overlay.set_message("Installing AI runtime...")
        self.app.processEvents()

        self.assertEqual(overlay.message, "Installing AI runtime...")
        self.assertFalse(overlay.isHidden())
        self.assertEqual(overlay.geometry(), host.rect())

        host.resize(800, 600)
        self.app.processEvents()
        self.assertEqual(overlay.geometry(), host.rect())

        overlay.set_message(None)
        self.assertTrue(overlay.isHidden())
        self.assertEqual(overlay.message, "")
        host.close()


if __name__ == "__main__":
    unittest.main()
