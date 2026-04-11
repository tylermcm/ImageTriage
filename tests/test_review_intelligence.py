from __future__ import annotations

import unittest
from unittest.mock import patch

from image_triage.metadata import CaptureMetadata
from image_triage.models import ImageRecord
from image_triage.review_intelligence import _RecordFingerprint, build_review_intelligence


class ReviewIntelligenceTests(unittest.TestCase):
    def test_exact_duplicate_records_are_grouped(self) -> None:
        records = [
            ImageRecord(path="C:/shots/a.raw", name="a.raw", size=1024, modified_ns=1),
            ImageRecord(path="C:/shots/b.raw", name="b.raw", size=1024, modified_ns=2),
            ImageRecord(path="C:/shots/c.raw", name="c.raw", size=900, modified_ns=3),
        ]
        fingerprints = {
            records[0].path: _RecordFingerprint(
                record=records[0],
                source_path=records[0].path,
                metadata=CaptureMetadata(path=records[0].path, width=4000, height=3000),
                dhash=None,
                avg_luma=0.0,
                width=4000,
                height=3000,
                sha1_digest="match",
            ),
            records[1].path: _RecordFingerprint(
                record=records[1],
                source_path=records[1].path,
                metadata=CaptureMetadata(path=records[1].path, width=4000, height=3000),
                dhash=None,
                avg_luma=0.0,
                width=4000,
                height=3000,
                sha1_digest="match",
            ),
            records[2].path: _RecordFingerprint(
                record=records[2],
                source_path=records[2].path,
                metadata=CaptureMetadata(path=records[2].path, width=4000, height=3000),
                dhash=None,
                avg_luma=0.0,
                width=4000,
                height=3000,
                sha1_digest="different",
            ),
        }

        with patch(
            "image_triage.review_intelligence._build_fingerprint",
            side_effect=lambda record, _metadata_cache: fingerprints[record.path],
        ):
            bundle = build_review_intelligence(records)

        group = bundle.groups[0]
        insight_a = bundle.insight_for_path(records[0].path)
        insight_b = bundle.insight_for_path(records[1].path)
        insight_c = bundle.insight_for_path(records[2].path)

        self.assertEqual(len(bundle.groups), 1)
        self.assertEqual(group.kind, "exact_duplicate")
        self.assertEqual(group.member_paths, (records[0].path, records[1].path))
        self.assertIsNotNone(insight_a)
        self.assertIsNotNone(insight_b)
        assert insight_a is not None
        assert insight_b is not None
        self.assertTrue(insight_a.is_exact_duplicate)
        self.assertEqual(insight_a.group_label, "Exact Dup")
        self.assertEqual(insight_b.rank_text, "2/2")
        self.assertIsNone(insight_c)

    def test_likely_duplicate_frames_get_near_duplicate_label(self) -> None:
        records = [
            ImageRecord(path="C:/shots/frame_01.jpg", name="frame_01.jpg", size=1500, modified_ns=1),
            ImageRecord(path="C:/shots/frame_02.jpg", name="frame_02.jpg", size=1510, modified_ns=2),
        ]
        fingerprints = {
            records[0].path: _RecordFingerprint(
                record=records[0],
                source_path=records[0].path,
                metadata=CaptureMetadata(path=records[0].path, width=4000, height=3000),
                dhash=0b1111000011110000,
                avg_luma=92.0,
                width=4000,
                height=3000,
                sha1_digest="a",
            ),
            records[1].path: _RecordFingerprint(
                record=records[1],
                source_path=records[1].path,
                metadata=CaptureMetadata(path=records[1].path, width=4000, height=3000),
                dhash=0b1111000011110001,
                avg_luma=95.0,
                width=4000,
                height=3000,
                sha1_digest="b",
            ),
        }

        with patch(
            "image_triage.review_intelligence._build_fingerprint",
            side_effect=lambda record, _metadata_cache: fingerprints[record.path],
        ):
            bundle = build_review_intelligence(records)

        self.assertEqual(len(bundle.groups), 1)
        self.assertEqual(bundle.groups[0].kind, "likely_duplicate")
        self.assertEqual(bundle.groups[0].label, "Near Dup")
        self.assertIn("Very small visual difference", bundle.groups[0].reasons[0])

        insight = bundle.insight_for_path(records[0].path)
        self.assertIsNotNone(insight)
        assert insight is not None
        self.assertTrue(insight.is_likely_duplicate)
        self.assertEqual(insight.summary_text, "Near Dup 1/2")


if __name__ == "__main__":
    unittest.main()
