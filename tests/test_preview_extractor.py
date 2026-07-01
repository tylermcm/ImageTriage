from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from aiculler.features import PreviewExtractor


class PreviewExtractorTests(unittest.TestCase):
    def test_regular_image_preview_applies_exif_orientation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "portrait.jpg"
            exif = Image.Exif()
            exif[274] = 6
            Image.new("RGB", (40, 20), "red").save(source, "JPEG", exif=exif)

            target, size = PreviewExtractor(root / "cache").extract(source)

            self.assertEqual((20, 40), size)
            with Image.open(target) as preview:
                self.assertEqual((20, 40), preview.size)

    def test_embedded_preview_applies_raw_container_orientation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "preview.jpg"
            payload_io = BytesIO()
            Image.new("RGB", (40, 20), "blue").save(payload_io, "JPEG")

            _, size = PreviewExtractor(root / "cache")._write_embedded_preview(
                payload_io.getvalue(),
                target,
                orientation=6,
            )

            self.assertEqual((20, 40), size)
            with Image.open(target) as preview:
                self.assertEqual((20, 40), preview.size)


if __name__ == "__main__":
    unittest.main()
