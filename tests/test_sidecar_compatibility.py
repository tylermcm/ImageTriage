from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from image_triage.library_store import _iter_catalog_candidate_folders
from image_triage.scanner import normalize_filesystem_path
from image_triage.xmp import _sidecar_candidates, existing_sidecar_paths, load_sidecar_annotation


class SidecarCompatibilityTests(unittest.TestCase):
    def test_recursive_catalog_prunes_os_and_nas_metadata_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_catalog_walk_") as temp_dir:
            root = Path(temp_dir)
            photos = root / "Photos"
            photos.mkdir()
            (photos / "photo.jpg").write_bytes(b"photo")
            ignored = [root / "@eaDir", root / "__MACOSX", root / ".thumbnails", root / ".Trash-1000"]
            for directory in ignored:
                directory.mkdir()
                (directory / "cached-thumbnail.jpg").write_bytes(b"thumbnail")

            candidates = set(_iter_catalog_candidate_folders(str(root)))

            self.assertEqual({normalize_filesystem_path(photos)}, candidates)

    def test_xmp_candidates_include_lowercase_and_uppercase_extensions(self) -> None:
        raw_candidates = _sidecar_candidates("/photos/DSC_0001.NEF")
        jpeg_candidates = _sidecar_candidates("/photos/DSC_0002.JPG")

        self.assertIn("/photos/DSC_0001.xmp", [path.replace("\\", "/") for path in raw_candidates])
        self.assertIn("/photos/DSC_0001.XMP", [path.replace("\\", "/") for path in raw_candidates])
        self.assertIn("/photos/DSC_0002.JPG.xmp", [path.replace("\\", "/") for path in jpeg_candidates])
        self.assertIn("/photos/DSC_0002.JPG.XMP", [path.replace("\\", "/") for path in jpeg_candidates])

    def test_existing_uppercase_xmp_is_bundled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_upper_xmp_") as temp_dir:
            image_path = Path(temp_dir) / "DSC_0001.NEF"
            sidecar_path = Path(temp_dir) / "DSC_0001.XMP"
            image_path.write_bytes(b"raw")
            sidecar_path.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="4" />
  </rdf:RDF>
</x:xmpmeta>
""",
                encoding="utf-8",
            )

            existing = existing_sidecar_paths(str(image_path))
            annotation = load_sidecar_annotation(str(image_path))

            self.assertEqual(1, len(existing))
            self.assertTrue(Path(existing[0]).exists())
            self.assertEqual(4, annotation.rating)


if __name__ == "__main__":
    unittest.main()
