"""Representative-crop scheduling in the Tag People dialog.

Flipping a filter used to leave the previous crop pass running on the single
crop thread: with a few hundred single-photo faces included, every card in the
new view stayed blank for as long as the stale pass took to drain.
"""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from aiculler.storage import SQLiteFeatureStore
from image_triage.people_search import assign_person_name, cluster_face_identities
from image_triage.quality.face import FaceRecord
from image_triage.quality.store import upsert_faces
from image_triage.ui.people_dialog import PeopleSearchDialog

# Two people with several faces each, plus single-photo faces that the
# "Include single-photo faces" switch shows and hides.
RECURRING = (6, 4)
SINGLETONS = 5


def _build_library(folder: Path) -> Path:
    db_path = folder / "features.sqlite"
    store = SQLiteFeatureStore(db_path)
    try:
        dims = len(RECURRING) + SINGLETONS
        counter = 0
        for index, size in enumerate(RECURRING + (1,) * SINGLETONS):
            base = [0.0] * dims
            base[index] = 1.0
            for member in range(size):
                counter += 1
                image_id = store.upsert_image(folder / f"img_{counter:03d}.jpg", status="ready")
                vector = list(base)
                vector[index] += member * 1e-4
                upsert_faces(
                    store.connection,
                    image_id,
                    [FaceRecord((10, 10, 90, 110), 0.9, identity_embedding=tuple(vector))],
                )
        store.connection.commit()
        clusters = sorted(
            cluster_face_identities(store.connection), key=lambda c: c.face_count, reverse=True
        )
        assign_person_name(store.connection, clusters[0].cluster_id, "Ada")
        store.connection.commit()
    finally:
        store.close()
    return db_path


class RepCropSchedulingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="people_crops_test_")
        self.folder = Path(self._temp.name)
        self.dialog = PeopleSearchDialog(_build_library(self.folder))

    def tearDown(self) -> None:
        self.dialog._teardown()
        try:
            self._temp.cleanup()
        except OSError:
            pass  # background writers may still hold the sqlite file on Windows

    def _settle(self) -> None:
        for _ in range(3):
            self.app.processEvents()

    def test_rebuilding_the_view_cancels_the_in_flight_pass(self) -> None:
        self.dialog.include_singles.setChecked(True)
        self._settle()
        stale = self.dialog._active_crop_task
        self.assertIsNotNone(stale, "showing singles should start a crop pass")

        # Toggling straight back rebuilds the visible set.
        self.dialog.include_singles.setChecked(False)
        self.assertTrue(stale._cancelled, "the stale pass must be cancelled, not queued behind")
        self.assertEqual({}, self.dialog._pending_rep_people)

    def test_stale_pass_finishing_does_not_clear_a_newer_one(self) -> None:
        self.dialog.include_singles.setChecked(True)
        self._settle()
        stale = self.dialog._active_crop_task
        self.dialog.include_singles.setChecked(False)
        self._settle()
        current = self.dialog._active_crop_task

        # The cancelled pass still reports in; it must not adopt the new one's slot.
        self.dialog._on_rep_crops_finished(stale)
        self.assertIs(current, self.dialog._active_crop_task)

    def test_decoded_faces_are_cached_and_reused(self) -> None:
        self.dialog.include_singles.setChecked(True)
        self._settle()
        card = self.dialog._cards[0]
        key = card.person.rep_key

        image = QImage(8, 8, QImage.Format.Format_RGB888)
        image.fill(0x336699)
        self.dialog._on_rep_crop(key, 0, image)
        self.assertIn(key, self.dialog._rep_cache)

        # Rebuilding the view must not re-queue a face we already decoded.
        self.dialog._populate_cards()
        self._settle()
        queued = self.dialog._active_crop_task
        if queued is not None:
            self.assertNotIn(key, {job[0] for job in queued.jobs})

        rebuilt = next(c for c in self.dialog._cards if c.person.rep_key == key)
        self.assertFalse(rebuilt.thumb.pixmap().isNull(), "cached face should paint immediately")


if __name__ == "__main__":
    unittest.main()
