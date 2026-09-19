from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from image_triage.file_associations import (
    ExtensionAssociationState,
    FileAssociationStatus,
    supported_file_association_suffixes,
)
import image_triage.ui.file_associations_dialog as dialog_module
from image_triage.ui.file_associations_dialog import (
    FileAssociationsDialog,
    _AssociationSwitch,
    file_type_category,
    file_type_name,
)


class FileAssociationsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.states = (
            ExtensionAssociationState(".jpg", True, True, "ImageTriage.SupportedImage"),
            ExtensionAssociationState(".png", False, False, "Applications\\IrfanView.exe"),
            ExtensionAssociationState(".nef", False, False, "Applications\\Photos.exe"),
            ExtensionAssociationState(".psd", False, False, ""),
        )
        self.status = FileAssociationStatus(
            command='"ImageTriage.exe" "%1"',
            supported_suffixes=tuple(state.suffix for state in self.states),
            registered_suffixes=(".jpg",),
            app_registered=True,
            windows_supported=True,
        )
        self.registered_calls: list[list[str]] = []
        self.removed_calls: list[list[str]] = []
        self.chooser_calls: list[str] = []
        self.originals = {
            "query_status": dialog_module.query_windows_file_association_status,
            "query_states": dialog_module.query_windows_file_association_states,
            "register": dialog_module.register_windows_file_associations,
            "remove": dialog_module.remove_windows_file_associations,
            "chooser": dialog_module.open_windows_file_association_chooser,
        }
        dialog_module.query_windows_file_association_status = lambda: self.status
        dialog_module.query_windows_file_association_states = lambda: self.states
        dialog_module.register_windows_file_associations = self._register
        dialog_module.remove_windows_file_associations = self._remove
        dialog_module.open_windows_file_association_chooser = self.chooser_calls.append
        self.dialog = FileAssociationsDialog()

    def tearDown(self) -> None:
        self.dialog.close()
        dialog_module.query_windows_file_association_status = self.originals["query_status"]
        dialog_module.query_windows_file_association_states = self.originals["query_states"]
        dialog_module.register_windows_file_associations = self.originals["register"]
        dialog_module.remove_windows_file_associations = self.originals["remove"]
        dialog_module.open_windows_file_association_chooser = self.originals["chooser"]

    def _register(self, suffixes=None):
        self.registered_calls.append(list(suffixes or ()))
        return self.status

    def _remove(self, suffixes=None):
        self.removed_calls.append(list(suffixes or ()))
        return self.status

    def test_rebuilt_manager_matches_the_four_column_status_layout(self) -> None:
        self.assertEqual("Image Triage File Association Manager", self.dialog.windowTitle())
        self.assertEqual(4, self.dialog.table.columnCount())
        self.assertEqual(
            ["Extension & Type", "Registration Status", "Current Default Application", "Manage Default"],
            [self.dialog.table.horizontalHeaderItem(index).text() for index in range(4)],
        )
        self.assertEqual(4, self.dialog.table.rowCount())
        self.assertIn("Registered File Types", self.dialog.registered_count_label.text())
        self.assertEqual(1, self.dialog.registration_progress.value())
        self.assertEqual(4, self.dialog.registration_progress.maximum())
        self.assertFalse(hasattr(self.dialog, "register_selected_button"))
        self.assertFalse(hasattr(self.dialog, "remove_selected_button"))
        self.assertEqual("Configure All", self.dialog.default_apps_button.text())
        self.dialog.show()
        self.app.processEvents()
        self.assertEqual(self.dialog.register_all_button.y(), self.dialog.close_button.y())

    def test_search_and_type_filter_rebuild_the_visible_rows(self) -> None:
        self.dialog.search_field.setText("nikon")
        self.assertEqual([".nef"], self.dialog._row_suffixes)

        self.dialog.search_field.clear()
        self.dialog.category_combo.setCurrentIndex(self.dialog.category_combo.findData("raw"))
        self.assertEqual([".nef"], self.dialog._row_suffixes)

        self.dialog.category_combo.setCurrentIndex(self.dialog.category_combo.findData("mixed"))
        self.assertEqual([".psd"], self.dialog._row_suffixes)

    def test_row_switch_and_configure_button_target_their_extension(self) -> None:
        switch = self.dialog.table.cellWidget(2, 1).findChild(_AssociationSwitch)
        self.assertFalse(switch.isChecked())
        switch.click()
        self.assertEqual([[".nef"]], self.registered_calls)

        configure = self.dialog.table.cellWidget(2, 3).findChild(QPushButton, "associationConfigureButton")
        configure.click()
        self.assertEqual([".nef"], self.chooser_calls)

    def test_copy_report_contains_registered_count_and_launch_command(self) -> None:
        self.dialog._copy_status()
        report = QApplication.clipboard().text()

        self.assertIn("Registered File Types: 1 / 4", report)
        self.assertIn("ImageTriage.exe", report)
        self.assertIn(".jpg", report)

    def test_every_supported_extension_has_a_name_and_category(self) -> None:
        for suffix in supported_file_association_suffixes():
            with self.subTest(suffix=suffix):
                self.assertNotEqual("Image File", file_type_name(suffix))
                self.assertIn(file_type_category(suffix), {"photo", "raw", "mixed"})


if __name__ == "__main__":
    unittest.main()
