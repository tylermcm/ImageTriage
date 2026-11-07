"""Face Groups sidebar: which faces are shown, in what order, and how few."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from aiculler.storage import SQLiteFeatureStore
from image_triage.quality.face import FaceRecord
from image_triage.quality.store import upsert_faces
from image_triage.people_search import assign_person_name, cluster_face_identities
from image_triage.face_groups import FaceGroup, face_group_photo_paths, load_face_groups

# Cluster sizes; index 0 is the biggest so naming is easy to target.
SIZES = (7, 5, 4, 3, 2, 1)


def _build_library(folder: Path) -> Path:
    db_path = folder / "features.sqlite"
    store = SQLiteFeatureStore(db_path)
    try:
        counter = 0
        for index, size in enumerate(SIZES):
            base = [0.0] * len(SIZES)
            base[index] = 1.0
            for member in range(size):
                counter += 1
                image_id = store.upsert_image(folder / f"img_{counter:03d}.jpg", status="ready")
                vector = list(base)
                vector[index] += member * 1e-4
                upsert_faces(
                    store.connection,
                    image_id,
                    [FaceRecord((4, 4, 40, 50), 0.9, identity_embedding=tuple(vector))],
                )
        store.connection.commit()
        clusters = sorted(
            cluster_face_identities(store.connection), key=lambda c: c.face_count, reverse=True
        )
        # Name the second-largest so name-before-size ordering is observable.
        assign_person_name(store.connection, clusters[1].cluster_id, "Ada")
        store.connection.commit()
    finally:
        store.close()
    return db_path


class LoadFaceGroupsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="face_groups_")
        self.folder = Path(self._temp.name)
        self.db_path = _build_library(self.folder)

    def tearDown(self) -> None:
        try:
            self._temp.cleanup()
        except OSError:
            pass

    def test_named_people_lead_even_when_smaller(self) -> None:
        groups = load_face_groups(self.db_path)
        self.assertTrue(groups)
        self.assertEqual("Ada", groups[0].name)
        self.assertEqual(5, groups[0].face_count)
        # ...and the larger unnamed cluster follows.
        self.assertFalse(groups[1].named)
        self.assertEqual(7, groups[1].face_count)

    def test_unnamed_are_ordered_by_recurrence(self) -> None:
        counts = [g.face_count for g in load_face_groups(self.db_path) if not g.named]
        self.assertEqual(sorted(counts, reverse=True), counts)

    def test_one_off_faces_are_left_out(self) -> None:
        counts = [g.face_count for g in load_face_groups(self.db_path)]
        self.assertNotIn(1, counts, "single-photo faces are noise in a sidebar")

    def test_respects_the_row_cap(self) -> None:
        self.assertEqual(2, len(load_face_groups(self.db_path, limit=2)))
        self.assertEqual([], load_face_groups(self.db_path, limit=0))

    def test_missing_database_is_not_an_error(self) -> None:
        self.assertEqual([], load_face_groups(self.folder / "nope.sqlite"))

    def test_photo_paths_are_distinct_and_match_the_count(self) -> None:
        group = next(g for g in load_face_groups(self.db_path) if g.named)
        paths = face_group_photo_paths(self.db_path, group.cluster_ids)
        self.assertEqual(group.face_count, len(paths))
        self.assertEqual(len(set(paths)), len(paths))

    def test_photo_paths_tolerate_bad_input(self) -> None:
        self.assertEqual([], face_group_photo_paths(self.db_path, ()))
        self.assertEqual([], face_group_photo_paths(self.folder / "nope.sqlite", (1,)))


class FaceGroupLabelTests(unittest.TestCase):
    def test_named_label_is_the_name(self) -> None:
        group = FaceGroup(key=1, name="Ada", cluster_ids=(1,), face_count=4)
        self.assertEqual("Ada", group.label)
        self.assertEqual("Ada", group.filter_label)

    def test_unnamed_label_carries_the_count(self) -> None:
        group = FaceGroup(key=2, name="", cluster_ids=(2,), face_count=3)
        self.assertIn("3", group.label)
        self.assertIn("Unnamed", group.filter_label)

    def test_singular_reads_correctly(self) -> None:
        group = FaceGroup(key=3, name="", cluster_ids=(3,), face_count=1)
        self.assertIn("1 photo)", group.label)




if __name__ == "__main__":
    unittest.main()
