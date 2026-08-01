from __future__ import annotations

import unittest

from image_triage.dino_prefilter import DINOPrefilterDecision
from image_triage.filtering import FileTypeFilter, RecordFilterQuery, matches_record_query
from image_triage.models import FilterMode, ImageRecord


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

    def test_matches_dino_prefilter_quick_filters(self) -> None:
        record = ImageRecord(
            path="C:/photos/bad.jpg",
            name="bad.jpg",
            size=1024,
            modified_ns=1,
        )

        removed = DINOPrefilterDecision(path=record.path, action="remove_from_pool")
        self.assertTrue(
            matches_record_query(
                record,
                RecordFilterQuery(quick_filter=FilterMode.DINO_REMOVED),
                dino_decision=removed,
            )
        )
        self.assertTrue(
            matches_record_query(
                record,
                RecordFilterQuery(quick_filter=FilterMode.AI_PREFILTER_DUMPED),
                dino_decision=removed,
            )
        )

    def test_prefilter_dumped_keeps_historical_quarantine_rows_visible(self) -> None:
        record = ImageRecord(path="C:/photos/old.jpg", name="old.jpg", size=1024, modified_ns=1)

        self.assertTrue(
            matches_record_query(
                record,
                RecordFilterQuery(quick_filter=FilterMode.AI_PREFILTER_DUMPED),
                dino_decision=DINOPrefilterDecision(path=record.path, action="quarantine"),
            )
        )


if __name__ == "__main__":
    unittest.main()
