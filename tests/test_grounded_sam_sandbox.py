from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from sandboxes.grounded_sam.core import (
    DEFAULT_CATEGORY_ALIASES,
    Detection,
    category_aliases,
    category_score_thresholds,
    canonical_label,
    filter_detections,
    merge_masks,
    parse_categories,
)
from sandboxes.grounded_sam.reporting import write_index, write_report


class GroundedSamSandboxTests(unittest.TestCase):
    def test_category_parser_defaults_normalizes_and_deduplicates(self) -> None:
        self.assertEqual(tuple(DEFAULT_CATEGORY_ALIASES), parse_categories(None))
        self.assertEqual(
            ("sky", "water", "buildings"),
            parse_categories(" Sky, water, SKY, Buildings "),
        )

    def test_grounding_phrase_maps_to_editor_category(self) -> None:
        aliases = {
            "water": ("water", "lake", "river"),
            "people": ("person", "people", "woman"),
        }
        self.assertEqual("water", canonical_label("a river", aliases))
        self.assertEqual("people", canonical_label("woman", aliases))
        self.assertIsNone(canonical_label("car", aliases))

    def test_refined_prompts_describe_regions_and_remove_ambiguous_cliff(self) -> None:
        aliases = category_aliases(("trees", "mountains"), profile="refined")

        self.assertIn("forest", aliases["trees"])
        self.assertIn("mountain range", aliases["mountains"])
        self.assertNotIn("cliff", aliases["mountains"])
        self.assertEqual("mountains", canonical_label("a mountain range", aliases))
        self.assertEqual("mountains", canonical_label("mountain", aliases))
        self.assertEqual("trees", canonical_label("tree", aliases))

    def test_refined_thresholds_trade_scene_recall_for_mountain_precision(self) -> None:
        thresholds = category_score_thresholds(
            ("sky", "trees", "water", "mountains"), 0.25, profile="refined"
        )

        self.assertAlmostEqual(0.22, thresholds["sky"])
        self.assertAlmostEqual(0.23, thresholds["trees"])
        self.assertAlmostEqual(0.25, thresholds["water"])
        self.assertAlmostEqual(0.30, thresholds["mountains"])

    def test_detection_filter_is_class_aware_and_keeps_best_overlap(self) -> None:
        detections = [
            Detection("water", "water", 0.90, (10, 10, 80, 80)),
            Detection("water", "lake", 0.70, (12, 12, 82, 82)),
            Detection("people", "person", 0.80, (10, 10, 80, 80)),
            Detection("trees", "tree", 0.99, (0, 0, 1, 1)),
        ]

        kept = filter_detections(
            detections,
            image_size=(100, 100),
            nms_threshold=0.5,
            minimum_area_fraction=0.001,
        )

        self.assertEqual(["water", "people"], [item.label for item in kept])
        self.assertEqual(0.90, kept[0].score)

    def test_detection_filter_applies_category_score_floor(self) -> None:
        detections = [
            Detection("sky", "cloudy sky", 0.23, (0, 0, 100, 40)),
            Detection("mountains", "mountain range", 0.27, (10, 10, 90, 60)),
        ]

        kept = filter_detections(
            detections,
            image_size=(100, 100),
            minimum_scores={"sky": 0.22, "mountains": 0.30},
        )

        self.assertEqual(["sky"], [item.label for item in kept])

    def test_masks_for_one_category_are_soft_max_merged(self) -> None:
        first = np.zeros((6, 8), dtype=np.float32)
        first[1:4, 1:4] = 0.7
        second = np.zeros((6, 8), dtype=np.float32)
        second[2:5, 3:7] = 0.9
        detections = [
            Detection("water", "water", 0.9, (1, 1, 4, 4)),
            Detection("water", "lake", 0.8, (3, 2, 7, 5)),
        ]

        merged = merge_masks(detections, [first, second], [0.85, 0.80])

        self.assertEqual({"water"}, set(merged))
        self.assertEqual(2, len(merged["water"].detections))
        self.assertAlmostEqual(0.9, float(merged["water"].soft_mask[3, 3]))
        self.assertGreater(merged["water"].coverage, 0.0)

    def test_report_records_masks_timings_and_model_provenance(self) -> None:
        rgb = np.full((30, 40, 3), 100, dtype=np.uint8)
        soft = np.zeros((30, 40), dtype=np.float32)
        soft[5:25, 10:30] = 0.9
        detection = Detection("water", "lake", 0.88, (10, 5, 30, 25))
        masks = merge_masks([detection], [soft], [0.91])

        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            report = write_report(
                output,
                Path("example.jpg"),
                rgb,
                detections=(detection,),
                masks=masks,
                timings_ms={
                    "decode": 1.0,
                    "grounding": 2.0,
                    "sam_encode": 3.0,
                    "sam_decode": 4.0,
                    "total": 10.0,
                },
                device="cpu",
                grounding_load_ms=5.0,
                sam_load_ms=6.0,
                settings={"categories": ["water"]},
            )
            index = write_index(output, [report])
            report_dir = Path(str(report["output_dir"]))
            payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))

            self.assertTrue(index.is_file())
            self.assertTrue((report_dir / "source.jpg").is_file())
            self.assertTrue((report_dir / "detections.jpg").is_file())
            self.assertTrue((report_dir / "masks" / "water.png").is_file())
            self.assertTrue((report_dir / "overlays" / "water.jpg").is_file())
            self.assertEqual("cpu", payload["device"])
            self.assertEqual(1, payload["masks"]["water"]["region_count"])
            self.assertIn("revision", payload["models"]["grounding"])
            self.assertIn("revision", payload["models"]["segmentation"])


if __name__ == "__main__":
    unittest.main()
