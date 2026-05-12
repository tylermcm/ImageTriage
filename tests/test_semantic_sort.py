from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from image_triage.models import ImageRecord
from image_triage.semantic_sort import (
    load_semantic_classifications,
    normalized_semantic_path_key,
    semantic_classification_for_record,
    semantic_folder_name,
)


class SemanticSortTests(unittest.TestCase):
    def test_load_semantic_classifications_indexes_ready_rows_by_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_semantic_") as temp_dir:
            image_path = Path(temp_dir) / "IMG_0001.jpg"
            csv_path = Path(temp_dir) / "semantic_classifications.csv"
            csv_path.write_text(
                "file_path,primary_label,primary_score,status\n"
                f"{image_path},portrait,0.92,ready\n",
                encoding="utf-8",
            )

            classifications = load_semantic_classifications(csv_path)

        classification = classifications[normalized_semantic_path_key(image_path)]
        self.assertEqual(classification.primary_label, "portrait")
        self.assertAlmostEqual(classification.primary_score, 0.92)
        self.assertTrue(classification.is_ready)

    def test_semantic_classification_for_record_matches_stack_members(self) -> None:
        primary = "C:/shots/IMG_0001.CR3"
        companion = "C:/shots/IMG_0001.JPG"
        record = ImageRecord(
            path=primary,
            name="IMG_0001.CR3",
            size=0,
            modified_ns=0,
            companion_paths=(companion,),
        )
        classifications = {
            normalized_semantic_path_key(companion): load_semantic_classifications_from_text(
                companion,
                "landscape",
            )
        }

        classification = semantic_classification_for_record(record, classifications)

        self.assertIsNotNone(classification)
        self.assertEqual(classification.primary_label, "landscape")

    def test_semantic_folder_name_sanitizes_windows_unsafe_labels(self) -> None:
        self.assertEqual(semantic_folder_name("Portrait / Ceremony"), "Portrait_Ceremony")
        self.assertEqual(semantic_folder_name("CON"), "CON_images")
        self.assertEqual(semantic_folder_name("   "), "unclassified")


def load_semantic_classifications_from_text(path: str, label: str):
    with tempfile.TemporaryDirectory(prefix="image_triage_semantic_row_") as temp_dir:
        csv_path = Path(temp_dir) / "semantic.csv"
        csv_path.write_text(
            "file_path,primary_label,primary_score,status\n"
            f"{path},{label},1.0,ready\n",
            encoding="utf-8",
        )
        return load_semantic_classifications(csv_path)[normalized_semantic_path_key(path)]


if __name__ == "__main__":
    unittest.main()
