import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QVBoxLayout

from aiculler.storage import SQLiteFeatureStore
from image_triage.people_search import cluster_face_identities, list_person_clusters
from image_triage.quality.face import FaceRecord
from image_triage.quality.store import upsert_faces
from image_triage.ui.people_dialog import _NameEdit, _NameSaveTask, _Person, _merge_people_stably


class PeopleDialogRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_existing_order_is_stable_and_new_people_append(self) -> None:
        existing = [
            _Person("", [10], 2, rep_key=10),
            _Person("Ada", [20], 3, rep_key=20),
        ]
        current = [
            _Person("New", [30], 12, rep_key=30),
            _Person("Ada", [20, 21], 6, rep_key=21),
            _Person("", [10], 4, rep_key=10),
        ]

        merged = _merge_people_stably(existing, current)

        self.assertEqual([[10], [20, 21], [30]], [person.cluster_ids for person in merged])
        self.assertEqual([4, 6, 12], [person.face_count for person in merged])
        self.assertEqual([10, 20, 30], [person.rep_key for person in merged])

    def test_named_person_keeps_position_when_cluster_ids_change(self) -> None:
        existing = [_Person("Ada", [7], 2, rep_key=7)]
        current = [_Person("Ada", [42], 3, rep_key=42)]

        merged = _merge_people_stably(existing, current)

        self.assertEqual(7, merged[0].rep_key)
        self.assertEqual([42], merged[0].cluster_ids)

    def test_return_in_name_editor_does_not_activate_dialog_default_button(self) -> None:
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        edit = _NameEdit(dialog)
        done = QPushButton("Done", dialog)
        done.setDefault(True)
        done.clicked.connect(dialog.accept)
        layout.addWidget(edit)
        layout.addWidget(done)
        submissions = []
        edit.submitted.connect(lambda: submissions.append(True))

        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
        )
        edit.keyPressEvent(event)

        self.assertEqual([True], submissions)
        self.assertEqual(QDialog.DialogCode.Rejected, dialog.result())
        self.assertTrue(event.isAccepted())

    def test_name_save_task_persists_cluster_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="people_name_") as temp_dir:
            db_path = Path(temp_dir) / "features.sqlite"
            store = SQLiteFeatureStore(db_path)
            try:
                image_id = store.upsert_image(Path(temp_dir) / "person.jpg", status="ready")
                upsert_faces(
                    store.connection,
                    image_id,
                    [FaceRecord((0, 0, 10, 10), 0.9, identity_embedding=(1.0, 0.0))],
                )
                store.connection.commit()
                cluster_id = cluster_face_identities(store.connection)[0].cluster_id
            finally:
                store.close()

            task = _NameSaveTask(str(db_path), [cluster_id], "Ada", "", cluster_id)
            failures = []
            task.signals.failed.connect(lambda _task, message: failures.append(message))
            task.run()

            self.assertEqual([], failures)
            store = SQLiteFeatureStore(db_path)
            try:
                self.assertEqual("Ada", list_person_clusters(store.connection)[0].name)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
