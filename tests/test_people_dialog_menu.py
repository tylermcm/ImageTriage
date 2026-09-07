"""Right-click menu on a person card in the Tag People dialog."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from aiculler.storage import SQLiteFeatureStore
from image_triage.people_search import (
    assign_person_name,
    cluster_face_identities,
    list_person_clusters,
)
from image_triage.quality.face import FaceRecord
from image_triage.quality.store import upsert_faces
from image_triage.ui.people_dialog import PeopleSearchDialog, _name_write_pool

SIZES = (5, 4, 3)


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
                    [FaceRecord((5, 5, 60, 70), 0.9, identity_embedding=tuple(vector))],
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


def _show_entry(actions):
    """The 'show photos' entry, found without pinning its exact wording."""
    return next(text for text in actions if text.startswith("Show "))


def _menu_actions(dialog, card):
    """Build the card's menu the way a right-click would, without showing it."""
    dialog._focus_card_for_menu(card)
    menu = dialog._build_person_menu(card)
    return {a.text(): a for a in menu.actions() if not a.isSeparator()}


class PersonMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="people_menu_")
        folder = Path(self._temp.name)
        self.db_path = _build_library(folder)
        self.dialog = PeopleSearchDialog(self.db_path)
        self.dialog.include_singles.setChecked(True)
        self.dialog.show()
        for _ in range(3):
            self.app.processEvents()

    def tearDown(self) -> None:
        self.dialog._teardown()
        try:
            self._temp.cleanup()
        except OSError:
            pass

    def _card(self, *, named: bool):
        return next(c for c in self.dialog._cards if c.person.named is named)

    def test_named_person_offers_photo_search_and_clear(self) -> None:
        card = self._card(named=True)
        actions = _menu_actions(self.dialog, card)
        self.assertIn("Rename", actions)
        self.assertTrue(actions["Clear name"].isEnabled())
        show = _show_entry(actions)
        self.assertIn("Ada", show)
        self.assertIn(str(card.person.face_count), show)
        self.assertTrue(actions[show].isEnabled())

    def test_unnamed_person_can_still_be_browsed(self) -> None:
        card = self._card(named=False)
        actions = _menu_actions(self.dialog, card)
        self.assertIn("Name this person", actions)
        self.assertFalse(actions["Clear name"].isEnabled())
        # Filtering goes by the cluster's own photo paths, not by name, so an
        # unnamed face is just as browsable as a named one.
        show = _show_entry(actions)
        self.assertTrue(actions[show].isEnabled())
        self.assertIn(str(card.person.face_count), show)
        self.assertIn("this person", show)

    def test_unnamed_person_returns_its_photo_paths(self) -> None:
        card = self._card(named=False)
        actions = _menu_actions(self.dialog, card)
        actions[_show_entry(actions)].trigger()
        self.assertTrue(self.dialog.requested_person_label.startswith("Unnamed face"))
        self.assertEqual(
            card.person.face_count, len(self.dialog.requested_person_paths)
        )
        self.assertEqual(QDialog.DialogCode.Accepted, self.dialog.result())

    def test_show_all_photos_reports_the_person_and_closes(self) -> None:
        card = self._card(named=True)
        actions = _menu_actions(self.dialog, card)
        actions[_show_entry(actions)].trigger()
        self.assertEqual("Ada", self.dialog.requested_person_label)
        self.assertEqual(card.person.face_count, len(self.dialog.requested_person_paths))
        self.assertEqual(QDialog.DialogCode.Accepted, self.dialog.result())

    def test_clear_name_persists(self) -> None:
        card = self._card(named=True)
        actions = _menu_actions(self.dialog, card)
        actions["Clear name"].trigger()
        self.assertFalse(card.person.named)  # optimistic, applied immediately
        self.assertTrue(_name_write_pool().waitForDone(5000), "name write did not finish")
        for _ in range(3):
            self.app.processEvents()

        store = SQLiteFeatureStore(self.db_path)
        try:
            names = [c.name for c in list_person_clusters(store.connection)]
        finally:
            store.close()
        self.assertNotIn("Ada", names)

    def test_ignore_from_menu_removes_only_that_person(self) -> None:
        before = len(self.dialog._cards)
        card = self.dialog._cards[0]
        key = card.person.rep_key
        actions = _menu_actions(self.dialog, card)
        ignore = next(text for text in actions if text.startswith("Ignore this person"))
        actions[ignore].trigger()
        for _ in range(3):
            self.app.processEvents()
        self.assertEqual(before - 1, len(self.dialog._cards))
        self.assertNotIn(key, {c.person.rep_key for c in self.dialog._cards})
        self.assertTrue(self.dialog.undo_row.isVisible())

    def test_merge_entry_only_appears_with_a_multi_selection(self) -> None:
        card = self.dialog._cards[0]
        self.assertFalse(any(t.startswith("Merge") for t in _menu_actions(self.dialog, card)))

        self.dialog._cards[0].set_selected(True)
        self.dialog._cards[1].set_selected(True)
        self.dialog._update_selection_ui()
        actions = _menu_actions(self.dialog, self.dialog._cards[0])
        self.assertTrue(any(t.startswith("Merge 2 selected") for t in actions))

    def test_right_click_selects_an_unselected_card_alone(self) -> None:
        self.dialog._cards[1].set_selected(True)
        self.dialog._update_selection_ui()
        target = self.dialog._cards[0]
        _menu_actions(self.dialog, target)
        self.assertTrue(target.is_selected())
        self.assertEqual([target], self.dialog._selected_cards())


if __name__ == "__main__":
    unittest.main()
