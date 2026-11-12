from __future__ import annotations

import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from image_triage.archive_ops import _create_archive_from_entries, build_archive_entries, extract_archive


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

    def test_extract_refuses_to_replace_a_file_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "shots.zip"
            destination = root / "out"
            destination.mkdir()
            (destination / "b.txt").write_text("mine")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("a.txt", "theirs")
                archive.writestr("b.txt", "theirs")

            with self.assertRaisesRegex(FileExistsError, "already in the destination"):
                extract_archive(str(archive_path), str(destination))

            self.assertEqual((destination / "b.txt").read_text(), "mine")
            self.assertEqual(sorted(p.name for p in destination.iterdir()), ["b.txt"])

    def test_extract_tar_refuses_to_replace_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "b.txt").write_text("theirs")
            archive_path = root / "shots.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(root / "b.txt", arcname="b.txt")
            destination = root / "out"
            destination.mkdir()
            (destination / "b.txt").write_text("mine")

            with self.assertRaises(FileExistsError):
                extract_archive(str(archive_path), str(destination))

            self.assertEqual((destination / "b.txt").read_text(), "mine")

    def test_failed_extract_leaves_nothing_behind(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_path = root / "shots.zip"
            destination = root / "out"
            destination.mkdir()
            (destination / "keep.txt").write_text("mine")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("a.txt", "one")
                archive.writestr("sub/b.txt", "two")
                archive.writestr("sub/c.txt", "three")

            def fail_on_third(current, total, message):
                if current == 3:
                    raise OSError("disk full")

            with self.assertRaisesRegex(OSError, "disk full"):
                extract_archive(str(archive_path), str(destination), progress_callback=fail_on_third)

            self.assertEqual(sorted(p.name for p in destination.iterdir()), ["keep.txt"])

    def test_extract_into_an_empty_folder_still_works_for_each_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.txt").write_text("one")
            for key, name in (("zip", "x.zip"), ("7z", "x.7z"), ("tar_gz", "x.tar.gz")):
                created = _create_archive_from_entries(
                    build_archive_entries([str(root / "a.txt")]), str(root / name), archive_key=key
                )
                destination = root / f"out_{key}"
                extracted = extract_archive(created, str(destination))
                self.assertEqual([Path(p).name for p in extracted], ["a.txt"])
                self.assertEqual((destination / "a.txt").read_text(), "one")
                with self.assertRaises(FileExistsError):
                    extract_archive(created, str(destination))
                self.assertEqual((destination / "a.txt").read_text(), "one")


if __name__ == "__main__":
    unittest.main()
