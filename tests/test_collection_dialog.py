from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from image_triage.ui.collection_dialog import CollectionEditDialog
from image_triage.ui.collection_ring import COLLECTION_COLORS


class CollectionEditDialogTests(unittest.TestCase):
    """The New Collection / Edit Collection window: name, color, add new winners here, fill hints."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_new_collection_defaults(self) -> None:
        dialog = CollectionEditDialog()
        self.assertEqual("New Collection", dialog.windowTitle())
        self.assertEqual("Create", dialog.primary_button.text())
        self.assertFalse(dialog.primary_button.isEnabled(), "needs a name")
        self.assertTrue(dialog.add_winners_check.isChecked(), "new collections take new winners by default")
        self.assertEqual("", dialog.result_data().color)
        self.assertIn("Drop photos onto it in the sidebar", dialog.fill_hints.text())

    def test_name_is_required_and_must_be_new(self) -> None:
        dialog = CollectionEditDialog(existing_names=("Portfolio",))
        dialog.name_field.setText("  portfolio ")
        self.assertFalse(dialog.primary_button.isEnabled())
        self.assertTrue(dialog.name_error.isVisibleTo(dialog))
        self.assertIn("already have a collection", dialog.name_error.text())
        dialog.name_field.setText("Utah   Portfolio")
        self.assertTrue(dialog.primary_button.isEnabled())
        self.assertEqual("Utah Portfolio", dialog.result_data().name)

    def test_color_swatches_and_more(self) -> None:
        dialog = CollectionEditDialog(name="Prints")
        swatch = dialog._swatches[COLLECTION_COLORS[2][1]]
        swatch.click()
        self.assertEqual(COLLECTION_COLORS[2][1], dialog.result_data().color)
        with patch("image_triage.ui.collection_dialog.QColorDialog.getColor", return_value=QColor("#123456")):
            dialog.more_colors_button.click()
        self.assertEqual("#123456", dialog.result_data().color)
        self.assertTrue(dialog._swatches["#123456"].isChecked())

    def test_editing_shows_save_and_keeps_stored_fields(self) -> None:
        dialog = CollectionEditDialog(
            title="Edit Collection",
            name="Proofs",
            kind="Proofing Set",
            description="For the Harper wedding",
            color="#abcdef",
            add_new_winners=False,
        )
        self.assertEqual("Save", dialog.primary_button.text())
        self.assertFalse(dialog.add_winners_check.isChecked())
        self.assertTrue(dialog._swatches["#abcdef"].isChecked(), "a custom color gets its own swatch")
        result = dialog.result_data()
        self.assertEqual(("Proofs", "Proofing Set", "For the Harper wedding", "#abcdef", False),
                         (result.name, result.kind, result.description, result.color, result.add_new_winners))

    def test_w_hint_follows_the_switch(self) -> None:
        dialog = CollectionEditDialog(name="X")
        self.assertIn("Mark a winner and it goes in", dialog.fill_hints.text())
        dialog.add_winners_check.setChecked(False)
        self.assertIn("Marks a winner only", dialog.fill_hints.text())


if __name__ == "__main__":
    unittest.main()
