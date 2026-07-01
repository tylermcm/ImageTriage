from __future__ import annotations

import sqlite3
import unittest

from image_triage.quality.model import DimensionScores
from image_triage.quality.face import FaceRecord
from image_triage.quality.store import (
    fetch_faces,
    fetch_all_dimensions,
    fetch_dimensions,
    upsert_dimensions,
    upsert_faces,
)


class QualityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self) -> None:
        self.conn.close()

    def test_upsert_and_fetch_round_trip(self) -> None:
        scores = DimensionScores(
            sharpness=8.5, exposure=9.0, dynamic_range=6.4, noise=8.1,
            contrast=7.0, color_harmony=7.5, monochrome=False,
        )
        upsert_dimensions(self.conn, 42, scores)
        got = fetch_dimensions(self.conn, 42)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got.sharpness, 8.5)
        self.assertAlmostEqual(got.color_harmony, 7.5)
        self.assertFalse(got.monochrome)
        # Unset Phase-2 fields round-trip as None.
        self.assertIsNone(got.aesthetic)
        self.assertIsNone(got.blink)

    def test_upsert_updates_existing(self) -> None:
        upsert_dimensions(self.conn, 7, DimensionScores(sharpness=1.0, monochrome=True))
        upsert_dimensions(self.conn, 7, DimensionScores(sharpness=9.9, monochrome=False))
        got = fetch_dimensions(self.conn, 7)
        self.assertAlmostEqual(got.sharpness, 9.9)
        self.assertFalse(got.monochrome)
        # Still a single row.
        self.assertEqual(len(fetch_all_dimensions(self.conn)), 1)

    def test_fetch_missing_returns_none(self) -> None:
        self.assertIsNone(fetch_dimensions(self.conn, 999))

    def test_fetch_all(self) -> None:
        upsert_dimensions(self.conn, 1, DimensionScores(sharpness=5.0))
        upsert_dimensions(self.conn, 2, DimensionScores(sharpness=6.0))
        allrows = fetch_all_dimensions(self.conn)
        self.assertEqual(set(allrows), {1, 2})
        self.assertAlmostEqual(allrows[2].sharpness, 6.0)

    def test_does_not_clobber_caller_row_factory(self) -> None:
        self.conn.row_factory = sqlite3.Row
        upsert_dimensions(self.conn, 3, DimensionScores(sharpness=4.0))
        fetch_dimensions(self.conn, 3)
        self.assertIs(self.conn.row_factory, sqlite3.Row)

    def test_face_records_round_trip(self) -> None:
        faces = (
            FaceRecord(
                bbox=(1.0, 2.0, 11.0, 22.0),
                det_score=0.8,
                eye_sharpness=7.5,
                gender="M",
                age=31,
            ),
        )
        upsert_faces(self.conn, 9, faces)
        got = fetch_faces(self.conn, 9)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].bbox, (1.0, 2.0, 11.0, 22.0))
        self.assertAlmostEqual(got[0].det_score, 0.8)
        self.assertAlmostEqual(got[0].eye_sharpness, 7.5)
        self.assertEqual(got[0].gender, "M")
        self.assertEqual(got[0].age, 31)

    def test_face_upsert_replaces_existing_records(self) -> None:
        upsert_faces(self.conn, 10, (FaceRecord(bbox=(0, 0, 1, 1), det_score=0.1),))
        upsert_faces(self.conn, 10, ())
        self.assertEqual(fetch_faces(self.conn, 10), ())


if __name__ == "__main__":
    unittest.main()
