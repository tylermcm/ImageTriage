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
from image_triage.ui.face_groups import (
    FaceGroup,
    FaceGroupsPanel,
    face_group_photo_paths,
    load_face_groups,
)

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


class FaceGroupsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = FaceGroupsPanel()

    def tearDown(self) -> None:
        self.panel.shutdown()

    def _groups(self, count: int) -> list[FaceGroup]:
        # rep_face=None keeps these tests off the crop thread.
        return [
            FaceGroup(key=i, name=f"P{i}", cluster_ids=(i,), face_count=count - i)
            for i in range(count)
        ]

    def test_unindexed_folder_explains_itself(self) -> None:
        self.panel.set_groups([], has_index=False)
        self.assertEqual(1, self.panel.count())
        self.assertIn("indexed", self.panel.item(0).text())
        self.assertEqual(Qt.ItemFlag.NoItemFlags, self.panel.item(0).flags())

    def test_indexed_but_empty_says_so_differently(self) -> None:
        self.panel.set_groups([], has_index=True)
        self.assertIn("No recurring faces", self.panel.item(0).text())

    def test_rows_plus_a_handoff_to_the_full_dialog(self) -> None:
        self.panel.set_groups(self._groups(3), has_index=True)
        self.assertEqual(4, self.panel.count(), "3 faces plus the All people row")
        self.assertEqual(
            "All people...",
            self.panel.item(3).data(Qt.ItemDataRole.AccessibleTextRole),
        )

    def test_activating_a_face_emits_its_group(self) -> None:
        groups = self._groups(3)
        self.panel.set_groups(groups, has_index=True)
        seen = []
        self.panel.group_activated.connect(seen.append)
        self.panel.itemClicked.emit(self.panel.item(1))
        self.assertEqual([groups[1]], seen)

    def test_activating_all_people_asks_for_the_dialog(self) -> None:
        self.panel.set_groups(self._groups(2), has_index=True)
        asked = []
        self.panel.browse_all_requested.connect(lambda: asked.append(True))
        self.panel.itemClicked.emit(self.panel.item(2))
        self.assertEqual([True], asked)

    def test_rows_carry_an_explicit_height(self) -> None:
        from image_triage.ui.face_groups import THUMB_PX

        self.panel.set_groups(self._groups(3), has_index=True)
        hint = self.panel.item(0).sizeHint()
        # QSize(-1, h) is invalid and Qt discards it, which silently hands row
        # height back to the stylesheet; the width must be non-negative.
        self.assertTrue(hint.isValid(), "an invalid hint is discarded by Qt")
        self.assertGreaterEqual(hint.height(), THUMB_PX)

    def test_max_height_matches_the_rows_it_holds(self) -> None:
        self.panel.set_groups(self._groups(4), has_index=True)
        rows = sum(self.panel.item(i).sizeHint().height() for i in range(self.panel.count()))
        self.assertEqual(rows + 2 * self.panel.frameWidth() + 2, self.panel.maximumHeight())
        self.assertEqual(0, self.panel.minimumHeight(), "it must be free to shrink")

    def test_rebuilding_cancels_the_previous_crop_pass(self) -> None:
        with_faces = [
            FaceGroup(key=9, name="X", cluster_ids=(9,), face_count=3, rep_face=("a.jpg", (0, 0, 1, 1)))
        ]
        self.panel.set_groups(with_faces, has_index=True)
        stale = self.panel._crop_task
        self.assertIsNotNone(stale)
        self.panel.set_groups(self._groups(2), has_index=True)
        self.assertTrue(stale._cancelled, "a rebuild must not leave the old pass running")

    def test_search_filters_rows_and_clear_restores_them(self) -> None:
        groups = [
            FaceGroup(key=1, name="Justin", cluster_ids=(1,), face_count=8),
            FaceGroup(key=2, name="Alec", cluster_ids=(2,), face_count=5),
            FaceGroup(key=3, name="", cluster_ids=(3,), face_count=3),
        ]
        self.panel.set_groups(groups, has_index=True)

        self.panel.set_search_text("ale")
        self.assertEqual(2, self.panel.count(), "one match plus the All people row")
        self.assertEqual(2, self.panel.item(0).data(Qt.ItemDataRole.UserRole))

        self.panel.set_search_text("")
        self.assertEqual(4, self.panel.count())

    def test_person_rows_use_the_target_row_widget(self) -> None:
        self.panel.set_groups(self._groups(1), has_index=True)
        self.assertIsNotNone(self.panel.itemWidget(self.panel.item(0)))
        self.assertIsNotNone(self.panel.itemWidget(self.panel.item(1)))
        self.assertEqual("", self.panel.item(0).text())
        self.assertEqual("", self.panel.item(1).text())


if __name__ == "__main__":
    unittest.main()
