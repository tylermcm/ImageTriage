from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from image_triage.editor_copy import (
    SAVE_COPY_FILTERS,
    EditorCopyService,
    default_save_copy_path,
    normalize_save_copy_path,
    validate_save_copy_paths,
    write_edited_copy,
)
from image_triage.ui.photo_editor_panel import EditRecipe, PhotoEditorPanel


class EditorCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_uses_plain_save_and_save_copy_labels(self) -> None:
        panel = PhotoEditorPanel()
        self.assertEqual("Save", panel.save_button.text())
        self.assertEqual("Save Copy", panel.save_copy_button.text())
        panel.close()

    def test_save_persists_current_edits_as_the_source_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_editor_save_") as temp_dir:
            source = Path(temp_dir) / "photo.png"
            Image.new("RGB", (12, 8), (40, 60, 80)).save(source)
            panel = PhotoEditorPanel()
            panel.set_image(source)
            panel._recipe = EditRecipe.from_dict({"contrast": 15})

            panel.save_sidecar()

            self.assertTrue((Path(temp_dir) / "photo.edit.json").exists())
            self.assertEqual("Saved edits", panel.status_label.text())
            panel.close()

    def test_default_copy_name_is_non_colliding_stack_variant(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_editor_copy_") as temp_dir:
            source = Path(temp_dir) / "photo.nef"
            source.write_bytes(b"raw")
            (Path(temp_dir) / "photo_1.jpg").write_bytes(b"existing")
            self.assertEqual(Path(temp_dir) / "photo_2.jpg", default_save_copy_path(source))

    def test_missing_extension_uses_selected_file_type(self) -> None:
        target = normalize_save_copy_path("C:/photos/edited", SAVE_COPY_FILTERS[1])
        self.assertEqual(".png", target.suffix)

    def test_save_copy_refuses_to_overwrite_original(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_editor_copy_") as temp_dir:
            source = Path(temp_dir) / "photo.png"
            source.write_bytes(b"source")
            with self.assertRaisesRegex(ValueError, "cannot overwrite"):
                validate_save_copy_paths(source, source)

    def test_write_edited_copy_renders_pixels_without_touching_original(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_editor_copy_") as temp_dir:
            source = Path(temp_dir) / "photo.png"
            target = Path(temp_dir) / "photo_1.png"
            Image.new("RGB", (20, 12), (60, 80, 100)).save(source)
            original_bytes = source.read_bytes()

            written = write_edited_copy(
                source,
                target,
                EditRecipe.from_dict({"exposure": 1.0}),
                [],
            )

            self.assertEqual(target, written)
            self.assertTrue(target.exists())
            self.assertEqual(original_bytes, source.read_bytes())
            with Image.open(source) as original, Image.open(target) as edited:
                self.assertEqual(original.size, edited.size)
                self.assertNotEqual(original.getpixel((0, 0)), edited.convert("RGB").getpixel((0, 0)))

    def test_background_service_writes_copy_and_reports_completion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_editor_copy_service_") as temp_dir:
            source = Path(temp_dir) / "photo.png"
            target = Path(temp_dir) / "photo_1.png"
            Image.new("RGB", (20, 12), (30, 50, 70)).save(source)
            service = EditorCopyService()
            saved: list[tuple[str, str]] = []
            failed: list[tuple[str, str]] = []
            service.saved.connect(lambda src, dst: saved.append((src, dst)))
            service.failed.connect(lambda dst, error: failed.append((dst, error)))

            self.assertTrue(service.request(str(source), str(target), EditRecipe(), []))
            self.assertFalse(service.request(str(source), str(target), EditRecipe(), []))
            deadline = time.monotonic() + 5.0
            while not saved and not failed and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.003)

            self.assertEqual([], failed)
            self.assertEqual([(str(source), str(target))], saved)
            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
