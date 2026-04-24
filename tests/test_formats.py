from __future__ import annotations

import unittest

from image_triage.formats import FITS_SUFFIXES, IMAGE_SUFFIXES, suffix_for_path


class FormatTests(unittest.TestCase):
    def test_suffix_for_path_recognizes_composite_fits_suffixes(self) -> None:
        self.assertEqual(".fits.fz", suffix_for_path("M42.FITS.FZ"))
        self.assertEqual(".fit.gz", suffix_for_path("stack.fit.gz"))
        self.assertEqual(".jpg", suffix_for_path("preview.JPG"))

    def test_fits_suffixes_are_scannable_image_types(self) -> None:
        for suffix in (".fit", ".fits", ".fits.fz", ".fit.gz", ".fts"):
            self.assertIn(suffix, FITS_SUFFIXES)
            self.assertIn(suffix, IMAGE_SUFFIXES)


if __name__ == "__main__":
    unittest.main()
