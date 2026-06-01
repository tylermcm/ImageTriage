from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from image_triage.aiculler_global_store import GlobalAdapterLabelStore


class GlobalAdapterLabelStoreTests(unittest.TestCase):
    def test_upsert_and_load_labels_for_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_global_labels_") as temp_dir:
            db_path = Path(temp_dir) / "labels.sqlite"
            image_path = Path(temp_dir) / "Shoot" / "IMG_0001.NEF"
            store = GlobalAdapterLabelStore(db_path)
            try:
                store.upsert_label(image_path, "hero", weight=3, is_dispute=True)
                labels = store.labels_for_paths((str(image_path),))
            finally:
                store.close()

        self.assertIn(str(image_path), labels)
        label = labels[str(image_path)]
        self.assertEqual("hero", label.label)
        self.assertEqual(3.0, label.weight)
        self.assertTrue(label.is_dispute)

    def test_delete_label_removes_record(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_global_labels_") as temp_dir:
            db_path = Path(temp_dir) / "labels.sqlite"
            image_path = Path(temp_dir) / "IMG_0001.NEF"
            store = GlobalAdapterLabelStore(db_path)
            try:
                store.upsert_label(image_path, "reject")
                store.delete_label(image_path)
                labels = store.labels_for_paths((str(image_path),))
            finally:
                store.close()

        self.assertEqual({}, labels)

    def test_summary_counts_all_and_matching_labels(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_global_labels_") as temp_dir:
            db_path = Path(temp_dir) / "labels.sqlite"
            first_path = Path(temp_dir) / "Shoot" / "IMG_0001.NEF"
            second_path = Path(temp_dir) / "Shoot" / "IMG_0002.NEF"
            other_path = Path(temp_dir) / "Other" / "IMG_0003.NEF"
            store = GlobalAdapterLabelStore(db_path)
            try:
                store.upsert_label(first_path, "hero", weight=2, is_dispute=True)
                store.upsert_label(second_path, "reject", weight=1)
                store.upsert_label(other_path, "keep", weight=3)
                all_stats = store.summary()
                matching_stats = store.summary_for_paths((str(first_path), str(second_path)))
            finally:
                store.close()

        self.assertEqual(3, all_stats.total_count)
        self.assertEqual(1, all_stats.dispute_count)
        self.assertEqual(6.0, all_stats.weighted_count)
        self.assertEqual(2, matching_stats.total_count)
        self.assertEqual(1, matching_stats.dispute_count)
        self.assertEqual(3.0, matching_stats.weighted_count)


if __name__ == "__main__":
    unittest.main()
