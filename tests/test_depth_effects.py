from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from image_triage.depth_effects import composite_lens_blur, lens_blur_radius
from image_triage.editor_render import CpuEditorRenderBackend
from image_triage.image_resize import _pillow_from_qimage, _qimage_from_pillow
from image_triage.ui.photo_editor_panel import EditRecipe


def _striped(h: int = 120, w: int = 120) -> np.ndarray:
    """High-frequency vertical stripes: blur visibly drops the contrast."""
    cols = ((np.arange(w) // 2) % 2 * 255).astype(np.uint8)
    return np.repeat(np.tile(cols, (h, 1))[..., None], 3, axis=2)


def _contrast(patch: np.ndarray) -> float:
    return float(np.asarray(patch, dtype=np.float32).std())


class LensBlurTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rgb = _striped()
        # Depth gradient: left column nearest (255), right column farthest (0).
        self.depth = np.tile(
            np.linspace(255, 0, self.rgb.shape[1], dtype=np.uint8), (self.rgb.shape[0], 1)
        )

    def test_amount_zero_is_identity(self) -> None:
        out = composite_lens_blur(self.rgb, self.depth, amount=0)
        self.assertTrue(np.array_equal(out, self.rgb))

    def test_focus_plane_stays_sharp_and_far_blurs(self) -> None:
        # Focus near (1.0): the near (left) edge keeps its stripe contrast, the
        # far (right) edge loses it to blur.
        out = composite_lens_blur(self.rgb, self.depth, amount=90, focus=1.0)
        near_contrast = _contrast(out[:, :12])
        far_contrast = _contrast(out[:, -12:])
        self.assertGreater(near_contrast, 100.0)      # near edge still crisp
        self.assertLess(far_contrast, near_contrast * 0.6)  # far edge blurred out

    def test_radius_scales_with_amount_and_size(self) -> None:
        self.assertEqual(0.0, lens_blur_radius(0, (1000, 1000)))
        self.assertGreater(
            lens_blur_radius(50, (2000, 2000)), lens_blur_radius(50, (400, 400))
        )


class LensBlurRenderIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_backend_applies_lens_blur_from_a_depth_path(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            rgb = _striped()
            base_q = _qimage_from_pillow(Image.fromarray(rgb, "RGB"), target_size=QSize())
            depth = np.tile(
                np.linspace(255, 0, rgb.shape[1], dtype=np.uint8), (rgb.shape[0], 1)
            )
            depth_path = Path(tmp) / "depth.png"
            Image.fromarray(depth, "L").save(depth_path)

            backend = CpuEditorRenderBackend()
            spec = {"amount": 90, "focus": 1.0, "depth_path": str(depth_path)}
            out = np.asarray(
                _pillow_from_qimage(
                    backend.render(
                        base_q, EditRecipe(lensblur_amount=90), [], base_key=("k",), lensblur=spec
                    )
                ).convert("RGB")
            )
            # Near edge (focus) keeps its stripe contrast; far edge is blurred.
            near_contrast = _contrast(out[:, :12])
            far_contrast = _contrast(out[:, -12:])
            self.assertGreater(near_contrast, 100.0)
            self.assertLess(far_contrast, near_contrast * 0.6)

    def test_recipe_round_trips_lens_blur_fields(self) -> None:
        from dataclasses import asdict

        recipe = EditRecipe(lensblur_amount=70, lensblur_focus=0.4)
        restored = EditRecipe.from_dict(asdict(recipe))
        self.assertEqual(70, restored.lensblur_amount)
        self.assertEqual(0.4, restored.lensblur_focus)
        self.assertNotEqual(asdict(EditRecipe()), asdict(recipe))


if __name__ == "__main__":
    unittest.main()
