from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from image_triage.background_ops import blur_radius_for, composite_background
from image_triage.editor_render import CpuEditorRenderBackend
from image_triage.image_resize import _pillow_from_qimage, _qimage_from_pillow
from image_triage.ui.photo_editor_panel import EditRecipe


def _checker() -> np.ndarray:
    """A vivid image so a blurred background is obviously different."""
    rng = np.random.default_rng(7)
    return (rng.random((40, 40, 3)) * 255).astype(np.uint8)


class CompositeBackgroundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rgb = _checker()
        # Left half foreground (255), right half background (0).
        self.matte = np.zeros((40, 40), np.uint8)
        self.matte[:, :20] = 255

    def test_off_is_identity(self) -> None:
        out = composite_background(self.rgb, self.matte, mode="off")
        self.assertTrue(np.array_equal(out, self.rgb))

    def test_foreground_is_preserved_and_background_changes(self) -> None:
        out = composite_background(self.rgb, self.matte, mode="blur", amount=90)
        # Foreground (matte == 255) is untouched.
        self.assertTrue(np.array_equal(out[:, :20], self.rgb[:, :20]))
        # Background (matte == 0) is blurred, i.e. different from the source.
        self.assertGreater(
            np.abs(out[:, 20:].astype(int) - self.rgb[:, 20:].astype(int)).mean(), 3.0
        )

    def test_color_fills_the_background(self) -> None:
        out = composite_background(self.rgb, self.matte, mode="color", color="#ff0000")
        # Pure background pixels become the colour; foreground stays.
        self.assertTrue(np.array_equal(out[:, 25], np.tile([255, 0, 0], (40, 1))))
        self.assertTrue(np.array_equal(out[:, :20], self.rgb[:, :20]))

    def test_remove_lays_a_transparency_checkerboard(self) -> None:
        out = composite_background(self.rgb, self.matte, mode="remove")
        # Foreground preserved; background is only the two checker greys.
        self.assertTrue(np.array_equal(out[:, :20], self.rgb[:, :20]))
        bg = out[:, 20:].reshape(-1, 3)
        uniques = {tuple(px) for px in np.unique(bg, axis=0)}
        self.assertTrue(uniques.issubset({(200, 200, 200), (150, 150, 150)}), uniques)
        self.assertEqual(2, len(uniques))  # both checker tones present

    def test_blur_radius_scales_with_image_and_amount(self) -> None:
        self.assertEqual(0.0, blur_radius_for(0, (1000, 1000)))
        small = blur_radius_for(50, (400, 400))
        big = blur_radius_for(50, (2000, 2000))
        self.assertGreater(big, small)  # same amount, larger image -> larger radius


class BackgroundRenderIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_backend_applies_background_from_a_matte_path(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            rgb = _checker()
            base_q = _qimage_from_pillow(Image.fromarray(rgb, "RGB"), target_size=QSize())
            matte = np.zeros((40, 40), np.uint8)
            matte[:, :20] = 255
            matte_path = directory / "matte.png"
            Image.fromarray(matte, "L").save(matte_path)

            backend = CpuEditorRenderBackend()
            plain = _pillow_from_qimage(
                backend.render(base_q, EditRecipe(), [], base_key=("k",), background=None)
            )
            spec = {"mode": "color", "amount": 60, "color": "#00ff00", "matte_path": str(matte_path)}
            out = np.asarray(
                _pillow_from_qimage(
                    backend.render(base_q, EditRecipe(background_mode="color"), [], base_key=("k",), background=spec)
                ).convert("RGB")
            )
            # Background half is now green; foreground half matches the plain render.
            self.assertTrue(np.array_equal(out[:, 25], np.tile([0, 255, 0], (40, 1))))
            self.assertTrue(np.array_equal(out[:, :20], np.asarray(plain.convert("RGB"))[:, :20]))

    def test_missing_matte_path_leaves_the_image_untouched(self) -> None:
        rgb = _checker()
        base_q = _qimage_from_pillow(Image.fromarray(rgb, "RGB"), target_size=QSize())
        backend = CpuEditorRenderBackend()
        spec = {"mode": "blur", "amount": 60, "color": "#000000", "matte_path": "does_not_exist.png"}
        out = np.asarray(
            _pillow_from_qimage(
                backend.render(base_q, EditRecipe(background_mode="blur"), [], base_key=("k",), background=spec)
            ).convert("RGB")
        )
        self.assertTrue(np.array_equal(out, rgb))


class BackgroundRecipeTests(unittest.TestCase):
    def test_settings_round_trip_and_count_as_edits(self) -> None:
        from dataclasses import asdict

        recipe = EditRecipe(background_mode="blur", background_amount=80, background_color="#123456")
        restored = EditRecipe.from_dict(asdict(recipe))
        self.assertEqual("blur", restored.background_mode)
        self.assertEqual(80, restored.background_amount)
        self.assertEqual("#123456", restored.background_color)
        # A default recipe stays default; enabling background makes it non-default.
        self.assertEqual(asdict(EditRecipe()), asdict(EditRecipe()))
        self.assertNotEqual(asdict(EditRecipe()), asdict(recipe))


if __name__ == "__main__":
    unittest.main()
