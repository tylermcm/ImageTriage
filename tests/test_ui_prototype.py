from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QWidget

from image_triage.models import ImageRecord, SessionAnnotation
from image_triage.ui.generated_prototype import UIPrototypeWindow, collect_prototype_items, open_generated_ui_prototype


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeGrid:
    def __init__(self) -> None:
        self._selected_indexes = {1}

    def thumbnail_for(self, index: int):
        return None


class _FakeOwner(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.folder = "C:\\Photos\\Set"
        self._records = [
            ImageRecord(path=f"C:\\Photos\\Set\\image_{index}.jpg", name=f"image_{index}.jpg", size=1000, modified_ns=0)
            for index in range(3)
        ]
        self._annotations = {
            self._records[0].path: SessionAnnotation(winner=True),
            self._records[1].path: SessionAnnotation(reject=True),
        }
        self._ai_results_by_path = {"sentinel": object()}
        self.grid = _FakeGrid()


class UIPrototypeTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_app()

    def test_collect_prototype_items_uses_placeholders_without_owner(self) -> None:
        items = collect_prototype_items(None, limit=6)

        self.assertEqual(len(items), 6)
        self.assertTrue(items[0].selected)
        self.assertTrue(items[0].accepted)

    def test_collect_prototype_items_reads_owner_state_without_mutating(self) -> None:
        owner = _FakeOwner()
        annotations_before = dict(owner._annotations)
        ai_before = dict(owner._ai_results_by_path)
        selected_before = set(owner.grid._selected_indexes)

        items = collect_prototype_items(owner)

        self.assertEqual([item.name for item in items], ["image_0.jpg", "image_1.jpg", "image_2.jpg"])
        self.assertTrue(items[0].accepted)
        self.assertTrue(items[1].rejected)
        self.assertEqual(owner._annotations, annotations_before)
        self.assertEqual(owner._ai_results_by_path, ai_before)
        self.assertEqual(owner.grid._selected_indexes, selected_before)

    def test_open_generated_ui_prototype_does_not_change_owner_review_state(self) -> None:
        owner = _FakeOwner()
        folder_before = owner.folder
        annotations_before = dict(owner._annotations)
        ai_before = dict(owner._ai_results_by_path)
        selected_before = set(owner.grid._selected_indexes)

        window = open_generated_ui_prototype(owner)
        try:
            self.assertIsInstance(window, UIPrototypeWindow)
            self.assertEqual(owner.folder, folder_before)
            self.assertEqual(owner._annotations, annotations_before)
            self.assertEqual(owner._ai_results_by_path, ai_before)
            self.assertEqual(owner.grid._selected_indexes, selected_before)
            self.assertIs(open_generated_ui_prototype(owner), window)
        finally:
            window.close()
            if getattr(owner, "_ui_prototype_window", None) is window:
                owner._ui_prototype_window = None


if __name__ == "__main__":
    unittest.main()
