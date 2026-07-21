from __future__ import annotations

import unittest

from image_triage.formats import (
    FITS_SUFFIXES,
    IMAGE_SUFFIXES,
    is_appledouble_path,
    is_image_file_candidate,
    suffix_for_path,
)


class FormatTests(unittest.TestCase):
    def test_suffix_for_path_recognizes_composite_fits_suffixes(self) -> None:
        self.assertEqual(".fits.fz", suffix_for_path("M42.FITS.FZ"))
        self.assertEqual(".fit.gz", suffix_for_path("stack.fit.gz"))
        self.assertEqual(".jpg", suffix_for_path("preview.JPG"))

    def test_fits_suffixes_are_scannable_image_types(self) -> None:
        for suffix in (".fit", ".fits", ".fits.fz", ".fit.gz", ".fts"):
            self.assertIn(suffix, FITS_SUFFIXES)
            self.assertIn(suffix, IMAGE_SUFFIXES)

    def test_appledouble_detection_accepts_windows_and_macos_paths(self) -> None:
        windows_sidecar = r"K:\Photos\._DSC_8499.JPG"
        macos_sidecar = "/Volumes/Photos/._DSC_8499.JPG"
        windows_photo = r"K:\Photos\DSC_8499.JPG"
        macos_photo = "/Volumes/Photos/DSC_8499.JPG"

        for path in (windows_sidecar, macos_sidecar):
            with self.subTest(path=path):
                self.assertTrue(is_appledouble_path(path))
                self.assertFalse(is_image_file_candidate(path))

        for path in (windows_photo, macos_photo):
            with self.subTest(path=path):
                self.assertFalse(is_appledouble_path(path))
                self.assertTrue(is_image_file_candidate(path))

    def test_suffix_detection_accepts_windows_and_macos_paths(self) -> None:
        self.assertEqual(".nef", suffix_for_path(r"K:\Photos\DSC_7758.NEF"))
        self.assertEqual(".nef", suffix_for_path("/Volumes/Photos/DSC_7758.NEF"))


if __name__ == "__main__":
    unittest.main()
