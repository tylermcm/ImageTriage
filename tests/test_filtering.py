from __future__ import annotations

import unittest

from image_triage.filtering import FileTypeFilter, RecordFilterQuery, matches_record_query
from image_triage.models import ImageRecord


class FilteringTests(unittest.TestCase):
    def test_matches_fits_file_type_filter(self) -> None:
        record = ImageRecord(
            path="C:/astro/M42.fits.fz",
            name="M42.fits.fz",
            size=1024,
            modified_ns=1,
        )

        self.assertTrue(matches_record_query(record, RecordFilterQuery(file_type=FileTypeFilter.FITS)))
        self.assertFalse(matches_record_query(record, RecordFilterQuery(file_type=FileTypeFilter.JPEG)))


if __name__ == "__main__":
    unittest.main()
