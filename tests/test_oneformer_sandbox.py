from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from sandboxes.oneformer.core import (
    MODEL_ID,
    category_for_raw_label,
    merge_app_categories,
    parse_categories,
    raw_class_coverage,
)
from sandboxes.oneformer.reporting import write_index, write_report
from sandboxes.oneformer.cli import discover_images


class OneFormerSandboxTests(unittest.TestCase):
    def test_folder_discovery_excludes_internal_generated_images(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.jpg"
            original.touch()
            generated = root / ".image_triage_ai" / "aiculler_cache" / "cached.jpg"
            generated.parent.mkdir(parents=True)
            generated.touch()

            self.assertEqual([original], discover_images(root, limit=12))

    def test_categories_default_normalize_and_reject_unknown_values(self) -> None:
        self.assertEqual(8, len(parse_categories(None)))
        self.assertEqual(("sky", "water"), parse_categories(" Sky, water, SKY "))
        with self.assertRaises(ValueError):
            parse_categories("road")

    def test_ade_labels_map_to_editor_categories(self) -> None:
        self.assertEqual("buildings", category_for_raw_label("building, edifice"))
        self.assertEqual("trees", category_for_raw_label("tree"))
        self.assertEqual("foliage", category_for_raw_label("plant, flora"))
        self.assertEqual("water", category_for_raw_label("swimming_pool"))
        self.assertEqual("water", category_for_raw_label("falls"))
        self.assertEqual("water", category_for_raw_label("pool"))
        self.assertEqual("mountains", category_for_raw_label("hill"))
        self.assertIsNone(category_for_raw_label("road, route"))

    def test_semantic_classes_merge_without_overlap(self) -> None:
        segmentation = np.array(
            [
                [2, 2, 4, 4],
                [21, 21, 16, 16],
                [12, 1, 9, 0],
            ],
            dtype=np.int16,
        )
        labels = {
            0: "wall",
            1: "building, edifice",
            2: "sky",
            4: "tree",
            9: "grass",
            12: "person",
            16: "mountain",
            21: "water",
        }

        masks = merge_app_categories(segmentation, labels, minimum_coverage=0.0)

        self.assertEqual(
            {"sky", "trees", "foliage", "water", "mountains", "people", "buildings"},
            set(masks),
        )
        combined = sum((result.mask for result in masks.values()), np.zeros_like(segmentation))
        self.assertLessEqual(float(combined.max()), 1.0)
        self.assertAlmostEqual(2 / 12, masks["water"].coverage)

    def test_raw_coverage_is_sorted_and_uses_string_keys(self) -> None:
        segmentation = np.array([[2, 2, 2], [1, 1, 0]], dtype=np.int16)
        records = raw_class_coverage(segmentation, {"0": "wall", "1": "building", "2": "sky"})

        self.assertEqual("sky", records[0].label)
        self.assertAlmostEqual(0.5, records[0].coverage)

    def test_report_records_masks_raw_classes_and_provenance(self) -> None:
        rgb = np.full((20, 30, 3), 100, dtype=np.uint8)
        segmentation = np.zeros((20, 30), dtype=np.int16)
        segmentation[:10] = 2
        masks = merge_app_categories(segmentation, {0: "wall", 2: "sky"})
        classes = raw_class_coverage(segmentation, {0: "wall", 2: "sky"})

        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            report = write_report(
                output,
                Path("example.jpg"),
                rgb,
                segmentation=segmentation,
                masks=masks,
                raw_classes=classes,
                timings_ms={"inference": 2.0, "total": 5.0},
                device="cpu",
                processor_load_ms=3.0,
                model_load_ms=4.0,
                settings={"task": "semantic"},
            )
            index = write_index(output, [report])
            report_dir = Path(str(report["output_dir"]))
            payload = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))

            self.assertTrue(index.is_file())
            self.assertTrue((report_dir / "masks" / "sky.png").is_file())
            self.assertTrue((report_dir / "semantic_class_ids.png").is_file())
            self.assertTrue((report_dir / "all_ade_classes.jpg").is_file())
            self.assertEqual(MODEL_ID, payload["model"]["id"])
            self.assertEqual("MIT", payload["model"]["license"])
            self.assertEqual(
                {"sky", "wall"},
                {entry["label"] for entry in payload["raw_classes"]},
            )


if __name__ == "__main__":
    unittest.main()
