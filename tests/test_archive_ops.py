from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from image_triage.archive_ops import extract_archive


class ArchiveOpsTests(unittest.TestCase):
    def test_extract_zip_rejects_too_many_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "many.zip"
            destination = root / "out"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("one.txt", "one")

            with patch("image_triage.archive_ops.MAX_ARCHIVE_ENTRY_COUNT", 0):
                with self.assertRaisesRegex(ValueError, "too many entries"):
                    extract_archive(str(archive_path), str(destination))

    def test_extract_zip_rejects_uncompressed_size_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "large.zip"
            destination = root / "out"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("one.txt", "one")

            with patch("image_triage.archive_ops.MAX_ARCHIVE_UNCOMPRESSED_BYTES", 1):
                with self.assertRaisesRegex(ValueError, "too large"):
                    extract_archive(str(archive_path), str(destination))

    def test_extract_zip_rejects_symlink_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "symlink.zip"
            destination = root / "out"
            info = zipfile.ZipInfo("link")
            info.external_attr = 0o120777 << 16
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(info, "target")

            with self.assertRaisesRegex(ValueError, "symbolic links"):
                extract_archive(str(archive_path), str(destination))


if __name__ == "__main__":
    unittest.main()
