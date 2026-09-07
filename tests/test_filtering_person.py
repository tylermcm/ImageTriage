"""Filtering the grid by a face cluster.

Browsing one person's photos keys off the cluster's own image paths rather than
the person's name, so a face nobody has named yet is just as browsable.
"""
import os
import unittest

from image_triage.filtering import RecordFilterQuery, active_filter_labels, matches_record_query
from image_triage.models import ImageRecord


def _key(path: str) -> str:
    return os.path.normpath(os.path.abspath(path)).casefold()


def _record(path: str) -> ImageRecord:
    return ImageRecord(path=path, name=os.path.basename(path), size=10, modified_ns=1)


class PersonFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hit = _record(r"C:/photos/a.jpg")
        self.miss = _record(r"C:/photos/b.jpg")
        self.paths = frozenset({_key(self.hit.path)})

    def test_inactive_without_a_label(self) -> None:
        query = RecordFilterQuery()
        for record in (self.hit, self.miss):
            self.assertTrue(
                matches_record_query(record, query, person_match_paths=self.paths),
                "no label means no person filter, whatever paths are supplied",
            )

    def test_restricts_to_the_cluster_photos(self) -> None:
        query = RecordFilterQuery(person_label="Unnamed face (3 photos)")
        self.assertTrue(matches_record_query(self.hit, query, person_match_paths=self.paths))
        self.assertFalse(matches_record_query(self.miss, query, person_match_paths=self.paths))

    def test_needs_no_name_and_ignores_search_text_matching(self) -> None:
        # The label is display-only: it must not be matched against filenames.
        query = RecordFilterQuery(person_label="zzz-not-a-filename")
        self.assertTrue(matches_record_query(self.hit, query, person_match_paths=self.paths))

    def test_active_but_unresolved_matches_nothing(self) -> None:
        query = RecordFilterQuery(person_label="Someone")
        self.assertFalse(matches_record_query(self.hit, query, person_match_paths=frozenset()))

    def test_combines_with_a_text_search(self) -> None:
        query = RecordFilterQuery(person_label="Ada", search_text="b.jpg")
        # b.jpg matches the text but is not one of Ada's photos.
        self.assertFalse(matches_record_query(self.miss, query, person_match_paths=self.paths))

    def test_shows_up_as_an_active_filter(self) -> None:
        query = RecordFilterQuery(person_label="Ada")
        self.assertTrue(query.has_active_filters, "so Clear filters offers a way out")
        self.assertIn('People "Ada"', active_filter_labels(query))


if __name__ == "__main__":
    unittest.main()
