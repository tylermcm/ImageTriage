from __future__ import annotations

import unittest
import os

from image_triage.filtering import (
    FileTypeFilter,
    RecordFilterQuery,
    deserialize_filter_query,
    matches_record_query,
    serialize_filter_query,
)
from image_triage.models import FilterMode, ImageRecord, SessionAnnotation
from image_triage.prefilter_common import PrefilterDecision


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

    def test_prefilter_dumped_matches_pool_removals(self) -> None:
        record = ImageRecord(path="C:/photos/bad.jpg", name="bad.jpg", size=1024, modified_ns=1)
        removed = PrefilterDecision(path=record.path, action="remove_from_pool")
        query = RecordFilterQuery(quick_filter=FilterMode.AI_PREFILTER_DUMPED)

        self.assertTrue(matches_record_query(record, query, prefilter_decision=removed))
        self.assertFalse(matches_record_query(record, query))

    def test_saved_filter_with_a_removed_dino_mode_falls_back_to_all(self) -> None:
        query = deserialize_filter_query({"quick_filter": "DINO Removed"})

        self.assertEqual(FilterMode.ALL, query.quick_filter)

    def test_prefilter_dumped_keeps_historical_quarantine_rows_visible(self) -> None:
        record = ImageRecord(path="C:/photos/old.jpg", name="old.jpg", size=1024, modified_ns=1)

        self.assertTrue(
            matches_record_query(
                record,
                RecordFilterQuery(quick_filter=FilterMode.AI_PREFILTER_DUMPED),
                prefilter_decision=PrefilterDecision(path=record.path, action="quarantine"),
            )
        )

    def test_search_text_accepts_external_semantic_match_path(self) -> None:
        record = ImageRecord(path="C:/photos/IMG_0001.jpg", name="IMG_0001.jpg", size=1024, modified_ns=1)
        query = RecordFilterQuery(search_text="dog on beach")

        self.assertFalse(matches_record_query(record, query))
        self.assertTrue(
            matches_record_query(
                record,
                query,
                search_match_paths={os.path.normpath(os.path.abspath(record.path)).casefold()},
            )
        )

    def test_min_rating_filter_uses_session_annotation(self) -> None:
        record = ImageRecord(path="C:/photos/rated.jpg", name="rated.jpg", size=1024, modified_ns=1)
        query = RecordFilterQuery(min_rating=4)

        self.assertFalse(matches_record_query(record, query, annotation=SessionAnnotation(rating=3)))
        self.assertTrue(matches_record_query(record, query, annotation=SessionAnnotation(rating=4)))

    def test_folder_filter_matches_parent_path(self) -> None:
        record = ImageRecord(path="C:/photos/restaurant/IMG_0001.jpg", name="IMG_0001.jpg", size=1024, modified_ns=1)

        self.assertTrue(matches_record_query(record, RecordFilterQuery(folder_text="restaurant")))
        self.assertFalse(matches_record_query(record, RecordFilterQuery(folder_text="beach")))

    def test_serializes_folder_and_confidence_filters(self) -> None:
        query = RecordFilterQuery(folder_text="set-b", min_search_confidence=0.42)

        restored = deserialize_filter_query(serialize_filter_query(query))

        self.assertEqual("set-b", restored.folder_text)
        self.assertAlmostEqual(0.42, restored.min_search_confidence)


if __name__ == "__main__":
    unittest.main()
