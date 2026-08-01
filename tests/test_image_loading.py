from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


AICULLING_ROOT = Path(__file__).resolve().parents[1] / "AICullingPipeline"
if str(AICULLING_ROOT) not in sys.path:
    sys.path.insert(0, str(AICULLING_ROOT))

from app.data.image_loading import load_rgb_for_inference, resolve_inference_read_block_bytes


class ImageLoadingTests(unittest.TestCase):
    def test_read_block_environment_override_is_bounded(self) -> None:
        previous = os.environ.get("AICULLING_IMAGE_READ_BLOCK_KB")
        try:
            os.environ["AICULLING_IMAGE_READ_BLOCK_KB"] = "256"
            self.assertEqual(resolve_inference_read_block_bytes(), 256 * 1024)
            os.environ["AICULLING_IMAGE_READ_BLOCK_KB"] = "1"
            self.assertEqual(resolve_inference_read_block_bytes(), 64 * 1024)
        finally:
            if previous is None:
                os.environ.pop("AICULLING_IMAGE_READ_BLOCK_KB", None)
            else:
                os.environ["AICULLING_IMAGE_READ_BLOCK_KB"] = previous

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

    def test_profile_reports_logical_io_without_changing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.jpg"
            Image.new("RGB", (800, 400), color=(10, 20, 30)).save(path, quality=90)
            profile: dict[str, object] = {}
            baseline = load_rgb_for_inference(
                path,
                target_short_edge=100,
                decode_scale=2,
            )

            image = load_rgb_for_inference(
                path,
                target_short_edge=100,
                decode_scale=2,
                profile=profile,
            )
            try:
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.size, baseline.size)
                self.assertEqual(image.tobytes(), baseline.tobytes())
                self.assertEqual(profile["image_format"], "JPEG")
                self.assertEqual(profile["frame_count"], 1)
                self.assertEqual(profile["decoder_read_block_bytes"], 1024 * 1024)
                self.assertGreater(profile["read_calls"], 0)
                self.assertGreater(profile["logical_read_bytes"], 0)
                self.assertLessEqual(
                    profile["unique_logical_read_bytes"],
                    profile["logical_read_bytes"],
                )
                self.assertIn("resize_wall_ms", profile)
                self.assertIn("resize_thread_cpu_ms", profile)
                phase_read_keys = {
                    key
                    for key in profile
                    if key.startswith("phase_") and key.endswith("_read_wall_ms")
                }
                self.assertTrue(phase_read_keys)
            finally:
                image.close()
                baseline.close()

    def test_larger_decoder_block_reduces_jpeg_read_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "noise.jpg"
            source = Image.effect_noise((2400, 1600), 100).convert("RGB")
            source.save(path, quality=95)
            source.close()
            small_profile: dict[str, object] = {}
            large_profile: dict[str, object] = {}

            small = load_rgb_for_inference(
                path,
                target_short_edge=224,
                read_block_bytes=64 * 1024,
                profile=small_profile,
            )
            large = load_rgb_for_inference(
                path,
                target_short_edge=224,
                read_block_bytes=1024 * 1024,
                profile=large_profile,
            )
            try:
                self.assertEqual(small.size, large.size)
                self.assertEqual(small.tobytes(), large.tobytes())
                self.assertLess(large_profile["read_calls"], small_profile["read_calls"])
                self.assertEqual(large_profile["decoder_read_block_bytes"], 1024 * 1024)
            finally:
                small.close()
                large.close()


if __name__ == "__main__":
    unittest.main()
