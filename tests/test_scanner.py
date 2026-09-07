from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

import image_triage.scanner as scanner_module
from image_triage.models import ImageRecord, ImageVariant, SortMode, sort_records
from image_triage.scanner import (
    _cached_records_need_raw_pair_refresh,
    discover_edited_paths,
    format_scan_error,
    scan_child_folders,
    scan_folder,
)


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
            variant_paths = {variant.path for variant in record.display_variants}
            self.assertEqual(_path_key(raw_path), _path_key(record.display_variants[0].path))
            self.assertEqual(raw_path.name, record.display_variants[0].name)
            self.assertNotIn(_path_key(root_companion), _path_set(variant_paths))
            self.assertIn(_path_key(root_edit), _path_set(variant_paths))
            self.assertIn(_path_key(nested_edit), _path_set(variant_paths))

    def test_scan_folder_exposes_raw_instead_of_jpeg_companion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            raw_path = root / "IMG_0010.NEF"
            jpeg_path = root / "IMG_0010.JPG"
            for path in (raw_path, jpeg_path):
                _write_image(path)

            record = scan_folder(str(root))[0]

            self.assertEqual(_path_key(raw_path), _path_key(record.display_variants[0].path))
            self.assertEqual(raw_path.name, record.display_variants[0].name)
            self.assertFalse(record.has_variant_stack)

    def test_legacy_jpeg_first_stack_is_exposed_as_raw_first(self) -> None:
        raw = r"C:\shoot\IMG_0011.CR3"
        jpeg = r"C:\shoot\IMG_0011.JPG"
        edit = r"C:\shoot\IMG_0011_1.jpg"
        record = ImageRecord(
            path=raw,
            name="IMG_0011.CR3",
            size=30_000,
            modified_ns=10,
            companion_paths=(jpeg,),
            edited_paths=(edit,),
            variants=(
                ImageVariant(jpeg, "IMG_0011.JPG", 5_000, 9),
                ImageVariant(edit, "IMG_0011_1.jpg", 6_000, 11),
            ),
        )

        self.assertEqual([raw, edit], [variant.path for variant in record.display_variants])
        self.assertEqual("IMG_0011.CR3", record.display_variants[0].name)

    def test_jpeg_folder_pairs_matching_raw_from_sibling_raw_files_folder(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            jpeg_folder = root / "jpg"
            raw_folder = root / "Raw Files"
            jpeg = jpeg_folder / "IMG_0020.JPG"
            raw = raw_folder / "IMG_0020.NEF"
            unmatched = jpeg_folder / "IMG_0021.JPG"
            unmatched_raw = raw_folder / "IMG_0099.NEF"
            for path in (jpeg, raw, unmatched, unmatched_raw):
                _write_image(path)

            records = scan_folder(str(jpeg_folder))
            by_name = {record.name: record for record in records}

            self.assertEqual({"IMG_0020.NEF", "IMG_0021.JPG"}, set(by_name))
            self.assertEqual(str(raw.resolve()), by_name["IMG_0020.NEF"].path)
            self.assertEqual((str(jpeg.resolve()),), by_name["IMG_0020.NEF"].companion_paths)

    def test_jpeg_only_cache_refreshes_once_when_sibling_raws_exist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            jpeg_folder = root / "jpg"
            raw_folder = root / "Raw Files"
            jpeg = jpeg_folder / "IMG_0030.JPG"
            raw = raw_folder / "IMG_0030.NEF"
            for path in (jpeg, raw):
                _write_image(path)
            stale = [ImageRecord(str(jpeg), jpeg.name, 10, 1)]
            refreshed = [ImageRecord(str(raw), raw.name, 20, 2, companion_paths=(str(jpeg),))]

            self.assertTrue(_cached_records_need_raw_pair_refresh(str(jpeg_folder), stale))
            self.assertFalse(_cached_records_need_raw_pair_refresh(str(jpeg_folder), refreshed))

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

    def test_scan_folder_ignores_macos_appledouble_image_sidecars(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            photo = root / "DSC_8499.JPG"
            raw = root / "DSC_7758.NEF"
            apple_double_jpeg = root / "._DSC_8499.JPG"
            apple_double_raw = root / "._DSC_7758.NEF"
            for path in (photo, raw, apple_double_jpeg, apple_double_raw):
                _write_image(path)

            records = scan_folder(str(root))

            self.assertEqual({photo.name, raw.name}, {record.name for record in records})
            self.assertNotIn(apple_double_jpeg.name, {record.name for record in records})
            self.assertNotIn(apple_double_raw.name, {record.name for record in records})

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

    def test_scan_folder_does_not_open_unrelated_protected_child_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            photo = root / "photo.jpg"
            protected = root / "Recovery"
            _write_image(photo)
            protected.mkdir()
            real_scandir = os.scandir

            def guarded_scandir(path):
                if _path_key(path) == _path_key(protected):
                    raise PermissionError(13, "Access is denied", str(protected))
                return real_scandir(path)

            scanner_module.os.scandir = guarded_scandir
            try:
                records = scan_folder(str(root))
            finally:
                scanner_module.os.scandir = real_scandir

            self.assertEqual([photo.name], [record.name for record in records])

    def test_scan_folder_skips_inaccessible_optional_companion_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            photo = root / "photo.jpg"
            companion_dir = root / "jpeg"
            _write_image(photo)
            companion_dir.mkdir()
            real_scandir = os.scandir

            def guarded_scandir(path):
                if _path_key(path) == _path_key(companion_dir):
                    raise PermissionError(13, "Access is denied", str(companion_dir))
                return real_scandir(path)

            scanner_module.os.scandir = guarded_scandir
            try:
                records = scan_folder(str(root))
            finally:
                scanner_module.os.scandir = real_scandir

            self.assertEqual([photo.name], [record.name for record in records])

    def test_scan_errors_are_human_readable(self) -> None:
        message = format_scan_error(PermissionError(13, "Access is denied", r"K:\Recovery"))

        self.assertEqual("Access to this folder is denied.", message)
        self.assertNotIn("WinError", message)

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

    def test_os_and_nas_metadata_directories_are_never_scanned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            visible = root / "Visible"
            ignored = [root / "@eaDir", root / "__MACOSX", root / ".thumbnails", root / "$RECYCLE.BIN"]
            visible.mkdir()
            for directory in ignored:
                directory.mkdir()
                _write_image(directory / "cached-thumbnail.jpg")

            child_records = scan_child_folders(str(root), include_hidden=True)

            self.assertEqual([visible.name], [record.name for record in child_records])
            for directory in ignored:
                with self.subTest(directory=directory.name):
                    self.assertEqual([], scan_folder(str(directory)))

    def test_editor_mask_assets_are_never_scanned_as_photos_or_folders(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            source = root / "portrait.jpg"
            asset_dir = root / "portrait.edit-assets"
            mask = asset_dir / "mask-001.png"
            _write_image(source)
            _write_image(mask)

            root_records = scan_folder(str(root))
            asset_records = scan_folder(str(asset_dir))
            child_folders = scan_child_folders(str(root), include_hidden=True)

            self.assertEqual([source.name], [record.name for record in root_records])
            self.assertEqual([], asset_records)
            self.assertNotIn(asset_dir.name, [record.name for record in child_folders])

    def test_hidden_edit_storage_root_is_never_scanned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scanner_") as temp_dir:
            root = Path(temp_dir)
            source = root / "portrait.jpg"
            edit_root = root / ".image_triage_edits"
            session = edit_root / "portrait.edit.json"
            mask = edit_root / "portrait.edit-assets" / "mask-001.png"
            _write_image(source)
            session.parent.mkdir(parents=True, exist_ok=True)
            session.write_text("{}")
            _write_image(mask)

            root_records = scan_folder(str(root))
            child_folders = scan_child_folders(str(root), include_hidden=True)

            self.assertEqual([source.name], [record.name for record in root_records])
            self.assertNotIn(edit_root.name, [record.name for record in child_folders])

    def test_sort_records_keeps_folders_before_images(self) -> None:
        folder = ImageRecord(path="C:/sample/B", name="B", size=0, modified_ns=1, is_folder=True)
        image = ImageRecord(path="C:/sample/A.jpg", name="A.jpg", size=100, modified_ns=999)

        for sort_mode in SortMode:
            with self.subTest(sort_mode=sort_mode):
                records = sort_records([image, folder], sort_mode)
                self.assertEqual(folder, records[0])


if __name__ == "__main__":
    unittest.main()
