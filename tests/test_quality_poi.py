from __future__ import annotations

import unittest

import numpy as np

from image_triage.quality.poi import PoiResult, focus_poi, should_use_smart_focus_crop


def _to_bgr(gray: np.ndarray) -> np.ndarray:
    return np.stack([gray, gray, gray], axis=-1)


def _gradient(h: int, w: int) -> np.ndarray:
    return np.tile(np.linspace(0, 255, w), (h, 1)).astype(np.uint8)


def _checkerboard(h: int, w: int, block: int = 4) -> np.ndarray:
    yy, xx = np.indices((h, w))
    return (((yy // block + xx // block) % 2) * 255).astype(np.uint8)


class FocusPoiTests(unittest.TestCase):
    def test_sharp_subject_on_soft_background_is_localized(self) -> None:
        # Smooth (low-detail) background with a sharp high-frequency patch.
        canvas = _gradient(200, 200)
        patch = _checkerboard(40, 40)
        canvas[40:80, 120:160] = patch  # subject at y~0.2-0.4, x~0.6-0.8
        result = focus_poi(_to_bgr(canvas))
        self.assertFalse(result.is_full_frame)
        self.assertIsNotNone(result.bbox)
        x0, y0, x1, y1 = result.bbox
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.assertTrue(0.55 < cx < 0.85, f"cx={cx}")
        self.assertTrue(0.15 < cy < 0.45, f"cy={cy}")
        self.assertGreater(result.confidence, 0.0)

    def test_uniform_sharp_is_full_frame(self) -> None:
        # Detail everywhere (deep DoF) -> no single subject.
        result = focus_poi(_to_bgr(_checkerboard(200, 200)))
        self.assertTrue(result.is_full_frame)
        self.assertIsNone(result.bbox)

    def test_flat_image_is_full_frame(self) -> None:
        result = focus_poi(_to_bgr(np.full((120, 120), 128, np.uint8)))
        self.assertTrue(result.is_full_frame)
        self.assertIsNone(result.bbox)

    def test_bbox_is_normalized(self) -> None:
        canvas = _gradient(150, 150)
        canvas[20:50, 20:50] = _checkerboard(30, 30)
        result = focus_poi(_to_bgr(canvas))
        if result.bbox is not None:
            for v in result.bbox:
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 1.0)

    def test_returns_poi_result(self) -> None:
        self.assertIsInstance(focus_poi(_to_bgr(_gradient(64, 64))), PoiResult)

    def test_landscape_profile_keeps_full_frame_even_with_poi(self) -> None:
        result = PoiResult((0.40, 0.55, 0.70, 0.85), 0.90, False)
        self.assertFalse(should_use_smart_focus_crop("landscape", result))

    def test_general_profile_rejects_bottom_foreground_texture(self) -> None:
        result = PoiResult((0.20, 0.58, 0.95, 1.00), 0.90, False)
        self.assertFalse(should_use_smart_focus_crop("uncategorized", result))

    def test_general_profile_allows_centered_subject_like_crop(self) -> None:
        result = PoiResult((0.38, 0.24, 0.62, 0.56), 0.90, False)
        self.assertTrue(should_use_smart_focus_crop("uncategorized", result))

    def test_wildlife_profile_allows_off_center_subject_crop(self) -> None:
        result = PoiResult((0.05, 0.20, 0.25, 0.45), 0.75, False)
        self.assertTrue(should_use_smart_focus_crop("wildlife", result))


if __name__ == "__main__":
    unittest.main()
