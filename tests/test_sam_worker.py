from __future__ import annotations

import unittest

from image_triage.sam_worker import _choose_mask_index


class SamMaskChoiceTests(unittest.TestCase):
    def test_prefers_the_whole_object_over_parts(self) -> None:
        # SAM returns [part, subpart, whole]; the largest (whole) wins.
        self.assertEqual(2, _choose_mask_index([100, 10, 500], frame=10_000))

    def test_skips_a_near_whole_frame_background_mask(self) -> None:
        # A mask covering ~95% of the frame is background; pick the next largest.
        self.assertEqual(2, _choose_mask_index([100, 9_500, 500], frame=10_000))

    def test_falls_back_to_largest_when_all_are_near_frame(self) -> None:
        self.assertEqual(1, _choose_mask_index([9_200, 9_600], frame=10_000))

    def test_single_candidate(self) -> None:
        self.assertEqual(0, _choose_mask_index([42], frame=10_000))

    def test_empty_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _choose_mask_index([], 100)


if __name__ == "__main__":
    unittest.main()
