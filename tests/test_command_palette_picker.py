from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSize
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from image_triage.ui.command_palette import CommandPaletteDialog, PaletteCommand


class CommandPalettePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_compact_anchored_picker_accepts_a_single_click(self) -> None:
        parent = QWidget()
        parent.resize(900, 700)
        anchor = QPushButton("Add", parent)
        anchor.setGeometry(420, 70, 60, 30)
        commands = [
            PaletteCommand(id=f"item.{index}", title=f"Button {index}", callback=lambda: None)
            for index in range(10)
        ]
        dialog = CommandPaletteDialog([], card_size=QSize(520, 420), parent=parent)
        results: list[int] = []
        dialog.finished.connect(results.append)
        dialog.configure(
            commands,
            card_size=QSize(520, 420),
            accept_on_click=True,
            compact_rows=True,
            anchor_widget=anchor,
        )

        parent.show()
        dialog.present()
        self.app.processEvents()

        self.assertEqual(420, dialog.card.height())
        anchor_bottom = anchor.mapToGlobal(QPoint(0, anchor.height())).y()
        gap = dialog.y() - anchor_bottom
        self.assertGreaterEqual(gap, 2)
        self.assertLessEqual(gap, 6)
        self.assertEqual(0, dialog.card.y())
        self.assertEqual(40, dialog.result_list.item(0).sizeHint().height())
        self.assertGreaterEqual(dialog.result_list.viewport().height() // 40, 7)

        first_item = dialog.result_list.item(0)
        dialog.result_list.itemClicked.emit(first_item)

        self.assertEqual([CommandPaletteDialog.DialogCode.Accepted], results)
        self.assertEqual("item.0", dialog.selected_command.id)
        self.assertFalse(dialog.isVisible())
        parent.close()

    def test_regular_palette_still_requires_activation(self) -> None:
        parent = QWidget()
        parent.resize(900, 700)
        command = PaletteCommand(id="item.regular", title="Regular", callback=lambda: None)
        dialog = CommandPaletteDialog([command], parent=parent)
        results: list[int] = []
        dialog.finished.connect(results.append)

        parent.show()
        dialog.present()
        self.app.processEvents()
        dialog.result_list.itemClicked.emit(dialog.result_list.item(0))

        self.assertEqual([], results)
        self.assertIsNone(dialog.selected_command)
        self.assertTrue(dialog.isVisible())
        dialog.reject()
        parent.close()

    def test_anchored_picker_reports_outside_dismissal(self) -> None:
        parent = QWidget()
        parent.resize(900, 700)
        anchor = QPushButton("Add", parent)
        dialog = CommandPaletteDialog([], parent=parent)
        results: list[int] = []
        dialog.finished.connect(results.append)
        dialog.configure(
            [PaletteCommand(id="item.dismiss", title="Dismiss", callback=lambda: None)],
            anchor_widget=anchor,
            compact_rows=True,
            accept_on_click=True,
        )

        parent.show()
        dialog.present()
        self.app.processEvents()
        dialog.hide()

        self.assertEqual([CommandPaletteDialog.DialogCode.Rejected], results)
        parent.close()


if __name__ == "__main__":
    unittest.main()
