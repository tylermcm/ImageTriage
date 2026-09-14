from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel

from image_triage.ui.collection_dialog import (
    COLLECTION_EXPLANATION,
    CollectionEditDialog,
)


class CollectionEditDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_creation_dialog_uses_compact_target_copy_and_correct_pluralization(self) -> None:
        singular = CollectionEditDialog(selection_count=1)
        plural = CollectionEditDialog(selection_count=3)

        self.assertEqual(singular.target_badge.text(), "Target: 1 selected bundle")
        self.assertEqual(plural.target_badge.text(), "Target: 3 selected bundles")
        self.assertNotIn("bundle(s)", singular.target_badge.text())
        self.assertEqual(singular.title_label.toolTip(), COLLECTION_EXPLANATION)
        self.assertFalse(
            any(label.text() == COLLECTION_EXPLANATION for label in singular.findChildren(QLabel))
        )

    def test_form_labels_are_stacked_directly_above_full_width_fields(self) -> None:
        dialog = CollectionEditDialog(selection_count=2)
        dialog.show()
        self.app.processEvents()

        for label, field in (
            (dialog.name_label, dialog.name_field),
            (dialog.purpose_label, dialog.kind_combo),
            (dialog.description_label, dialog.description_field),
        ):
            self.assertLess(label.geometry().bottom(), field.geometry().top())
            self.assertEqual(label.geometry().left(), field.geometry().left())
            self.assertEqual(dialog.name_field.width(), field.width())

        dialog.close()

    def test_purpose_is_an_explicit_combo_with_a_custom_disclosure_chevron(self) -> None:
        dialog = CollectionEditDialog()

        self.assertIsInstance(dialog.kind_combo, QComboBox)
        self.assertEqual(dialog.kind_combo.objectName(), "collectionPurposeCombo")
        self.assertGreater(dialog.kind_combo.count(), 1)
        dialog.kind_combo.resize(300, 34)
        self.assertFalse(dialog.kind_combo.grab().isNull())

    def test_primary_action_is_descriptive_rightmost_and_name_gated(self) -> None:
        dialog = CollectionEditDialog(selection_count=1)
        dialog.show()
        self.app.processEvents()

        self.assertEqual(dialog.primary_button.text(), "Create Collection")
        self.assertEqual(dialog.primary_button.objectName(), "collectionPrimaryButton")
        self.assertTrue(dialog.primary_button.isDefault())
        self.assertFalse(dialog.primary_button.isEnabled())
        self.assertGreater(
            dialog.primary_button.mapTo(dialog, dialog.primary_button.rect().topLeft()).x(),
            dialog.cancel_button.mapTo(dialog, dialog.cancel_button.rect().topLeft()).x(),
        )

        dialog.name_field.setText("  Portfolio   Maybes  ")
        self.assertTrue(dialog.primary_button.isEnabled())
        self.assertEqual(dialog.result_data().name, "Portfolio Maybes")
        dialog.close()

    def test_editing_uses_save_copy_and_preserves_result_contract(self) -> None:
        dialog = CollectionEditDialog(
            title="Edit Collection",
            name="Client Selects",
            kind="Proofing Set",
            description="Round two",
        )

        self.assertEqual(dialog.primary_button.text(), "Save Changes")
        self.assertEqual(dialog.target_badge.text(), "Target: Existing collection")
        result = dialog.result_data()
        self.assertEqual(result.name, "Client Selects")
        self.assertEqual(result.kind, "Proofing Set")
        self.assertEqual(result.description, "Round two")


if __name__ == "__main__":
    unittest.main()
