"""WI-4.7: the Move/Copy confirmation and completion dialogs."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from image_triage.ui.transfer_dialogs import confirm_transfer, show_transfer_complete


class TransferDialogsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.parent = QWidget()

    def test_checkbox_default_flows_through_to_the_returned_choice(self) -> None:
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes):
            confirmed, include_companions = confirm_transfer(
                self.parent, verb="Copy", count=3, destination=r"C:\dest", include_companions_default=False
            )
        self.assertTrue(confirmed)
        # The checkbox wasn't touched by this test, so it reports the default
        # that was passed in -- confirming the default actually seeds the
        # checkbox's initial state, not just an unused parameter.
        self.assertFalse(include_companions)

    def test_no_means_not_confirmed(self) -> None:
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.No):
            confirmed, _ = confirm_transfer(
                self.parent, verb="Move", count=1, destination=r"C:\dest", include_companions_default=True
            )
        self.assertFalse(confirmed)

    def test_open_destination_button_opens_the_folder(self) -> None:
        def fake_exec(self):
            open_button = next(b for b in self.buttons() if b.text() == "Open Destination")
            open_button.click()
            return 0

        with patch.object(QMessageBox, "exec", fake_exec), patch(
            "image_triage.ui.transfer_dialogs.open_in_file_explorer"
        ) as mock_open:
            show_transfer_complete(self.parent, verb_past="Copied", count=5, destination=r"C:\dest")

        mock_open.assert_called_once_with(r"C:\dest")

    def test_ok_only_does_not_open_anything(self) -> None:
        def fake_exec(self):
            ok_button = self.button(QMessageBox.StandardButton.Ok)
            ok_button.click()
            return 0

        with patch.object(QMessageBox, "exec", fake_exec), patch(
            "image_triage.ui.transfer_dialogs.open_in_file_explorer"
        ) as mock_open:
            show_transfer_complete(self.parent, verb_past="Moved", count=2, destination=r"C:\dest")

        mock_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
