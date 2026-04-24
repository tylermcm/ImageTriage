from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from image_triage.models import ImageRecord
from image_triage.scanner import discover_edited_paths, scan_folder


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image-triage-test")


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
                {str(root_companion), str(paired_companion)},
                set(record.companion_paths),
            )
            self.assertEqual(
                {str(root_edit), str(nested_edit)},
                set(record.edited_paths),
            )
            variant_paths = {variant.path for variant in record.variants}
            self.assertIn(str(root_companion), variant_paths)
            self.assertIn(str(root_edit), variant_paths)
            self.assertIn(str(nested_edit), variant_paths)

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
            self.assertEqual(str(primary), record.path)
            self.assertEqual(
                {str(edit_one), str(edit_two)},
                set(record.edited_paths),
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

            self.assertNotIn(str(existing_edit), discovered)
            self.assertIn(str(new_edit), discovered)
            self.assertIn(str(nested_new_edit), discovered)

    def test_scan_folder_includes_fits_variants(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            primary_fits = root / "m42.fits"
            compressed_fits = root / "andromeda.fits.fz"
            for path in (primary_fits, compressed_fits):
                _write_image(path)

            records = scan_folder(str(root))

            self.assertEqual({primary_fits.name, compressed_fits.name}, {record.name for record in records})
            self.assertEqual({str(primary_fits), str(compressed_fits)}, {record.path for record in records})


if __name__ == "__main__":
    unittest.main()
