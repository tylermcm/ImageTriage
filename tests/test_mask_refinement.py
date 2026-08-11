from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtGui import QColor, QImage

from cli_editor.photo_terminal.session import ValidationError, new_session, validate_session
from image_triage.mask_refinement import refine_bitmap_qimage, refine_mask_array
from image_triage.ui import mask_overlay
from image_triage.ui.mask_overlay import mask_strength_qimage


class GeneratedMaskRefinementTests(unittest.TestCase):
    @staticmethod
    def _square_mask() -> np.ndarray:
        mask = np.zeros((41, 41), dtype=np.float32)
        mask[12:29, 12:29] = 1.0
        return mask

    def test_shift_edge_expands_and_contracts_the_selection(self) -> None:
        mask = self._square_mask()
        expanded = refine_mask_array(mask, edge_radius=10, shift_edge=100)
        contracted = refine_mask_array(mask, edge_radius=10, shift_edge=-100)

        self.assertGreater(float(expanded.sum()), float(mask.sum()))
        self.assertLess(float(contracted.sum()), float(mask.sum()))

    def test_feather_creates_a_soft_transition_and_contrast_tightens_it(self) -> None:
        mask = self._square_mask()
        feathered = refine_mask_array(mask, feather=3)
        contrasted = refine_mask_array(mask, feather=3, contrast=80)
        soft = np.logical_and(feathered > 0.01, feathered < 0.99)

        self.assertGreater(int(np.count_nonzero(soft)), 0)
        self.assertGreater(
            float(np.mean(np.abs(contrasted[soft] - 0.5))),
            float(np.mean(np.abs(feathered[soft] - 0.5))),
        )

    def test_generated_mask_feather_softens_inside_and_outside_the_boundary(self) -> None:
        mask = self._square_mask()
        feathered = refine_mask_array(mask, feather=4)

        self.assertEqual(1.0, float(feathered[20, 20]))
        self.assertLess(float(feathered[12, 20]), 1.0)
        self.assertGreater(float(feathered[11, 20]), 0.0)
        self.assertEqual(0.0, float(mask[11, 20]))

    def test_feather_reach_matches_the_requested_pixel_radius(self) -> None:
        mask = np.zeros((401, 401), dtype=np.float32)
        mask[151:250, 151:250] = 1.0

        feathered = refine_mask_array(mask, feather=30)

        self.assertEqual(0.0, float(feathered[200, 120]))
        self.assertGreater(float(feathered[200, 121]), 0.0)
        self.assertLess(float(feathered[200, 180]), 1.0)
        self.assertEqual(1.0, float(feathered[200, 181]))

    def test_feather_does_not_reflect_the_selection_at_the_canvas_edge(self) -> None:
        mask = np.ones((101, 101), dtype=np.float32)

        feathered = refine_mask_array(mask, feather=30)

        self.assertLess(float(feathered[0, 0]), float(feathered[50, 50]))
        self.assertGreater(float(feathered[50, 50]), 0.999)

    def test_large_pixel_feather_has_a_materially_larger_reach(self) -> None:
        bitmap = QImage(512, 512, QImage.Format.Format_Grayscale8)
        bitmap.fill(QColor("black"))
        for y in range(176, 336):
            for x in range(176, 336):
                bitmap.setPixelColor(x, y, QColor("white"))

        feather_10 = refine_bitmap_qimage(bitmap, {"edgeFeather": 10.0})
        feather_1000 = refine_bitmap_qimage(bitmap, {"edgeFeather": 1000.0})

        self.assertLess(
            feather_1000.pixelColor(256, 256).red(),
            feather_10.pixelColor(256, 256).red(),
        )
        self.assertGreater(
            feather_1000.pixelColor(80, 256).red(),
            feather_10.pixelColor(80, 256).red(),
        )

    def test_smooth_removes_an_isolated_mask_speck(self) -> None:
        mask = self._square_mask()
        mask[3, 3] = 1.0
        smoothed = refine_mask_array(mask, smooth=25)

        self.assertEqual(0.0, float(smoothed[3, 3]))
        self.assertGreater(float(smoothed[20, 20]), 0.99)

    def test_edge_detection_uses_the_photo_as_a_guide(self) -> None:
        guide = np.zeros((41, 41, 3), dtype=np.uint8)
        guide[:, 21:] = 255
        mask = np.zeros((41, 41), dtype=np.float32)
        mask[:, :18] = 1.0
        mask[:, 18] = 0.8
        mask[:, 19] = 0.65
        mask[:, 20] = 0.55
        mask[:, 21] = 0.45
        mask[:, 22] = 0.3
        mask[:, 23] = 0.15

        refined = refine_mask_array(mask, guide_rgb=guide, edge_radius=6)

        self.assertFalse(np.allclose(mask, refined))
        self.assertGreater(
            float(np.mean(refined[:, 20] - refined[:, 21])),
            float(np.mean(mask[:, 20] - mask[:, 21])),
        )

    def test_refinement_failure_falls_back_to_the_original_bitmap(self) -> None:
        bitmap = QImage(12, 8, QImage.Format.Format_Grayscale8)
        bitmap.fill(QColor("white"))
        components = [
            (
                "bitmap",
                {
                    "_liveBitmap": bitmap,
                    "edgeDetectionRadius": 10,
                },
            )
        ]

        original_refine = mask_overlay.refine_bitmap_qimage

        def fail_refinement(*_args: object, **_kwargs: object) -> QImage:
            raise RuntimeError("test refinement failure")

        mask_overlay.refine_bitmap_qimage = fail_refinement
        try:
            strength = mask_strength_qimage(components, 12, 8, (12, 8))
        finally:
            mask_overlay.refine_bitmap_qimage = original_refine

        self.assertIsNotNone(strength)
        assert strength is not None
        self.assertEqual(255, strength.pixelColor(6, 4).red())

    def test_clear_selection_and_invert_are_applied_to_generated_bitmap(self) -> None:
        bitmap = QImage(12, 8, QImage.Format.Format_Grayscale8)
        bitmap.fill(QColor("white"))

        cleared = mask_strength_qimage(
            [("bitmap", {"_liveBitmap": bitmap, "selectionCleared": True})],
            12,
            8,
            (12, 8),
        )
        inverted = mask_strength_qimage(
            [
                (
                    "bitmap",
                    {
                        "_liveBitmap": bitmap,
                        "selectionCleared": True,
                        "invert": True,
                    },
                )
            ],
            12,
            8,
            (12, 8),
        )

        self.assertIsNotNone(cleared)
        self.assertIsNotNone(inverted)
        assert cleared is not None and inverted is not None
        self.assertEqual(0, cleared.pixelColor(6, 4).red())
        self.assertEqual(255, inverted.pixelColor(6, 4).red())

    def test_root_clear_and_invert_act_on_the_whole_group(self) -> None:
        """A click-to-select session is one mask built from several 'add'
        components; the root's clear/invert must act on the finished union, not
        just the root layer (per-component invert would flood the frame)."""
        left = QImage(20, 10, QImage.Format.Format_Grayscale8)
        left.fill(QColor("black"))
        for y in range(10):
            for x in range(0, 6):
                left.setPixel(x, y, QColor("white").rgb())
        right = QImage(20, 10, QImage.Format.Format_Grayscale8)
        right.fill(QColor("black"))
        for y in range(10):
            for x in range(14, 20):
                right.setPixel(x, y, QColor("white").rgb())

        def group(root_params):
            return mask_strength_qimage(
                [
                    ("bitmap", {"_liveBitmap": left, **root_params}, "add"),
                    ("bitmap", {"_liveBitmap": right}, "add"),
                ],
                20,
                10,
                (20, 10),
            )

        base = group({})
        assert base is not None
        self.assertEqual(255, base.pixelColor(2, 5).red())   # left block in
        self.assertEqual(255, base.pixelColor(17, 5).red())  # right block in
        self.assertEqual(0, base.pixelColor(10, 5).red())    # gap out

        inverted = group({"invert": True})
        assert inverted is not None
        # The whole union flips: both blocks drop out, the gap fills in.
        self.assertEqual(0, inverted.pixelColor(2, 5).red())
        self.assertEqual(0, inverted.pixelColor(17, 5).red())
        self.assertEqual(255, inverted.pixelColor(10, 5).red())

        cleared = group({"selectionCleared": True})
        assert cleared is not None
        # Clearing the root empties every component, not just the root.
        self.assertEqual(0, cleared.pixelColor(2, 5).red())
        self.assertEqual(0, cleared.pixelColor(17, 5).red())
        self.assertEqual(0, cleared.pixelColor(10, 5).red())

    def test_subject_mask_refinement_params_are_range_validated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_refinement_schema_") as temp_dir:
            source_path = Path(temp_dir) / "source.jpg"
            Image.new("RGB", (20, 12)).save(source_path)
            _session_path, session = new_session(source_path)
            session["masks"].append(
                {
                    "id": "mask-001",
                    "type": "subject-select",
                    "coordinateSpaceId": "space-source-full",
                    "cacheAssetId": "mask-001-cache",
                    "model": {
                        "id": "example/model",
                        "version": "revision",
                        "weightsHash": "sha256:test",
                    },
                    "params": {"edgeShift": 101},
                }
            )

            with self.assertRaisesRegex(ValidationError, "edgeShift"):
                validate_session(session)

    def test_subject_mask_feather_accepts_1000_pixels_but_not_more(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_feather_schema_") as temp_dir:
            source_path = Path(temp_dir) / "source.jpg"
            Image.new("RGB", (20, 12)).save(source_path)
            _session_path, session = new_session(source_path)
            mask = {
                "id": "mask-001",
                "type": "subject-select",
                "coordinateSpaceId": "space-source-full",
                "cacheAssetId": "mask-001-cache",
                "model": {
                    "id": "example/model",
                    "version": "revision",
                    "weightsHash": "sha256:test",
                },
                "params": {"edgeFeather": 1000},
            }
            session["masks"].append(mask)
            validate_session(session)
            mask["params"]["edgeFeather"] = 1000.1
            with self.assertRaisesRegex(ValidationError, "edgeFeather"):
                validate_session(session)


if __name__ == "__main__":
    unittest.main()
