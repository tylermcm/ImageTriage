from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import image_triage.depth_maps as depth_maps
from image_triage.depth_maps import depth_map_cache_path


class DepthMapCachePathTests(unittest.TestCase):
    """Tests for depth_map_cache_path: the headless-render lookup that checks
    the on-disk depth-map cache without ever running depth-model inference."""

    def test_returns_none_when_source_file_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_depth_cache_") as temp_dir:
            missing_source = Path(temp_dir) / "missing.jpg"

            self.assertIsNone(depth_map_cache_path(missing_source))

    def test_returns_none_when_not_cached(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_depth_cache_") as temp_dir:
            source = Path(temp_dir) / "photo.jpg"
            source.write_bytes(b"fake-image-bytes")
            cache_root = Path(temp_dir) / "cache"

            self.assertIsNone(depth_map_cache_path(source, cache_root=cache_root))

    def test_returns_cached_path_when_depth_png_exists(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_depth_cache_") as temp_dir:
            source = Path(temp_dir) / "photo.jpg"
            source.write_bytes(b"fake-image-bytes")
            cache_root = Path(temp_dir) / "cache"
            stat = source.resolve().stat()
            cache_key = depth_maps._source_cache_key(source.resolve(), stat.st_size, stat.st_mtime_ns)
            cache_dir = cache_root / cache_key
            cache_dir.mkdir(parents=True)
            depth_path = cache_dir / "depth.png"
            depth_path.write_bytes(b"fake-depth")

            result = depth_map_cache_path(source, cache_root=cache_root)

            self.assertEqual(depth_path, result)


if __name__ == "__main__":
    unittest.main()
