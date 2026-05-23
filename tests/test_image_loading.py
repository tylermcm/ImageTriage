from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


AICULLING_ROOT = Path(__file__).resolve().parents[1] / "AICullingPipeline"
if str(AICULLING_ROOT) not in sys.path:
    sys.path.insert(0, str(AICULLING_ROOT))

from app.data.image_loading import load_rgb_for_inference


class ImageLoadingTests(unittest.TestCase):
    def test_large_image_is_downsampled_for_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.jpg"
            Image.new("RGB", (800, 400), color=(10, 20, 30)).save(path, quality=90)

            image = load_rgb_for_inference(path, target_short_edge=100, decode_scale=2)
            try:
                self.assertEqual(image.mode, "RGB")
                self.assertLessEqual(min(image.size), 200)
                self.assertLessEqual(max(image.size), 800)
            finally:
                image.close()

    def test_extreme_panorama_long_edge_is_capped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "pano.jpg"
            Image.new("RGB", (2000, 200), color=(10, 20, 30)).save(path, quality=90)

            image = load_rgb_for_inference(
                path,
                target_short_edge=100,
                decode_scale=2,
                long_edge_multiplier=3,
            )
            try:
                self.assertEqual(image.mode, "RGB")
                self.assertLessEqual(max(image.size), 600)
            finally:
                image.close()


if __name__ == "__main__":
    unittest.main()
