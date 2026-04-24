from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from image_triage.bursts import find_burst_groups
from image_triage.metadata import CaptureMetadata
from image_triage.models import ImageRecord


class BurstTests(unittest.TestCase):
    def test_find_burst_groups_skips_fits_sequences(self) -> None:
        base_time = datetime(2022, 8, 24, 23, 23, 26)
        records = [
            ImageRecord(path="C:/astro/frame_01.fits", name="frame_01.fits", size=1, modified_ns=1),
            ImageRecord(path="C:/astro/frame_02.fits", name="frame_02.fits", size=1, modified_ns=2),
        ]
        metadata_by_path = {
            records[0].path: CaptureMetadata(path=records[0].path, captured_at_value=base_time, focal_length_value=400.0, aperture_value=2.8, iso_value=800.0),
            records[1].path: CaptureMetadata(path=records[1].path, captured_at_value=base_time + timedelta(seconds=1), focal_length_value=400.0, aperture_value=2.8, iso_value=800.0),
        }

        groups = find_burst_groups(records, metadata_by_path)

        self.assertEqual(groups, [])


if __name__ == "__main__":
    unittest.main()
