from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

from image_triage.models import ImageRecord, SortMode, sort_records
from image_triage.scanner import discover_edited_paths, scan_child_folders, scan_folder


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image-triage-test")


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _path_set(paths) -> set[str]:
    return {_path_key(path) for path in paths}


class ScannerTests(unittest.TestCase):
    def test_scan_folder_groups_raw_companions_and_edits(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "IMG_0001.CR3"
            root_companion = root / "IMG_0001.JPG"
            paired_companion = root / "jpeg" / "IMG_0001.jpg"
            root_edit = root / "IMG_0001_1.jpg"
            nested_edit = root / "edit" / "IMG_0001_2.jpg"
            for path in (raw_path, root_companion, paired_companion, root_edit, nested_edit):
                _write_image(path)

            records = scan_folder(str(root))

            self.assertEqual(1, len(records))
            record = records[0]
            self.assertEqual(raw_path.name, record.name)
            self.assertEqual(
                _path_set((root_companion, paired_companion)),
                _path_set(record.companion_paths),
            )
            self.assertEqual(
                _path_set((root_edit, nested_edit)),
                _path_set(record.edited_paths),
            )
            variant_paths = {variant.path for variant in record.variants}
            self.assertIn(_path_key(root_companion), _path_set(variant_paths))
            self.assertIn(_path_key(root_edit), _path_set(variant_paths))
            self.assertIn(_path_key(nested_edit), _path_set(variant_paths))

    def test_scan_folder_prefers_base_file_as_family_primary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            primary = root / "shot.jpg"
            edit_one = root / "shot_1.jpg"
            edit_two = root / "shot_2.jpg"
            for path in (primary, edit_one, edit_two):
                _write_image(path)

            records = scan_folder(str(root))

            self.assertEqual(1, len(records))
            record = records[0]
            self.assertEqual(_path_key(primary), _path_key(record.path))
            self.assertEqual(
                _path_set((edit_one, edit_two)),
                _path_set(record.edited_paths),
            )

    def test_discover_edited_paths_skips_existing_stack_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            primary = root / "IMG_0200.CR3"
            existing_edit = root / "IMG_0200_1.jpg"
            new_edit = root / "IMG_0200_2.jpg"
            nested_new_edit = root / "edit" / "IMG_0200_3.jpg"
            for path in (primary, existing_edit, new_edit, nested_new_edit):
                _write_image(path)

            record = ImageRecord(
                path=str(primary),
                name=primary.name,
                size=0,
                modified_ns=0,
                edited_paths=(str(existing_edit),),
            )
            discovered = discover_edited_paths(record)

            discovered_paths = _path_set(discovered)
            self.assertNotIn(_path_key(existing_edit), discovered_paths)
            self.assertIn(_path_key(new_edit), discovered_paths)
            self.assertIn(_path_key(nested_new_edit), discovered_paths)

    def test_scan_folder_includes_fits_variants(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            primary_fits = root / "m42.fits"
            compressed_fits = root / "andromeda.fits.fz"
            for path in (primary_fits, compressed_fits):
                _write_image(path)

            records = scan_folder(str(root))

            self.assertEqual({primary_fits.name, compressed_fits.name}, {record.name for record in records})
            self.assertEqual(_path_set((primary_fits, compressed_fits)), _path_set(record.path for record in records))

    def test_scan_child_folders_returns_folder_records(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            alpha = root / "Alpha"
            beta = root / "Beta"
            alpha.mkdir()
            beta.mkdir()
            _write_image(root / "zeta.jpg")

            records = scan_child_folders(str(root))

            self.assertEqual(["Alpha", "Beta"], [record.name for record in records])
            self.assertTrue(all(record.is_folder for record in records))
            self.assertEqual(_path_set((alpha, beta)), _path_set(record.path for record in records))

    def test_scan_child_folders_hides_dot_folders_by_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            visible = root / "Visible"
            hidden = root / ".image_triage_ai"
            visible.mkdir()
            hidden.mkdir()

            default_records = scan_child_folders(str(root))
            visible_records = scan_child_folders(str(root), include_hidden=True)

            self.assertEqual(["Visible"], [record.name for record in default_records])
            self.assertEqual([".image_triage_ai", "Visible"], [record.name for record in visible_records])

    def test_sort_records_keeps_folders_before_images(self) -> None:
        folder = ImageRecord(path="C:/sample/B", name="B", size=0, modified_ns=1, is_folder=True)
        image = ImageRecord(path="C:/sample/A.jpg", name="A.jpg", size=100, modified_ns=999)

        for sort_mode in SortMode:
            with self.subTest(sort_mode=sort_mode):
                records = sort_records([image, folder], sort_mode)
                self.assertEqual(folder, records[0])


if __name__ == "__main__":
    unittest.main()
