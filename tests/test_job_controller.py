from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow

from image_triage.job_controller import JobController, JobSpec


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class JobControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def test_start_progress_indeterminate_and_close(self) -> None:
        window = QMainWindow()
        controller = JobController(
            window,
            JobSpec(
                title="Resize Images",
                preparing_label="Preparing resize...",
                running_label="Resizing...",
                indeterminate_label="Finalizing...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
                minimum_width=480,
            ),
        )

        dialog = controller.start(5)
        self.assertEqual(dialog.windowTitle(), "Resize Images")
        self.assertEqual(dialog.maximum(), 5)
        self.assertTrue(dialog.windowModality() == Qt.WindowModality.NonModal)
        self.assertTrue(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertGreaterEqual(dialog.minimumWidth(), 480)

        controller.progress(3, 5, "Processing...")
        self.assertEqual(dialog.value(), 3)
        self.assertEqual(dialog.labelText(), "Processing...")

        controller.indeterminate()
        self.assertEqual(dialog.minimum(), 0)
        self.assertEqual(dialog.maximum(), 0)
        self.assertEqual(dialog.labelText(), "Finalizing...")

        controller.close()
        QApplication.processEvents()
        self.assertFalse(dialog.isVisible())
        window.close()

    def test_start_reuses_dialog_and_resets_when_range_shrinks(self) -> None:
        window = QMainWindow()
        controller = JobController(
            window,
            JobSpec(
                title="Catalog",
                preparing_label="Preparing...",
                running_label="Running...",
            ),
        )

        dialog_first = controller.start(8)
        dialog_first.setValue(7)
        dialog_second = controller.start(4)

        self.assertIs(dialog_first, dialog_second)
        self.assertEqual(dialog_second.maximum(), 4)
        self.assertLessEqual(dialog_second.value(), 0)

        controller.close()
        QApplication.processEvents()
        window.close()


if __name__ == "__main__":
    unittest.main()
