from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from image_triage.ui.prototype_style import FolderTreeView


class _FileInfo:
    def __init__(self, root: bool) -> None:
        self._root = root

    def isRoot(self) -> bool:  # noqa: N802 - mirrors QFileInfo
        return self._root


class _FolderModel(QStandardItemModel):
    def fileInfo(self, index):  # noqa: N802 - mirrors QFileSystemModel
        return _FileInfo(not index.parent().isValid())

    def filePath(self, index):  # noqa: N802 - mirrors QFileSystemModel
        return str(index.data(Qt.ItemDataRole.UserRole) or "")


class FolderTreeExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.model = _FolderModel()
        self.tree = FolderTreeView()
        self.tree.setModel(self.model)
        for drive_name in ("C:", "D:"):
            drive = QStandardItem(drive_name)
            drive.setData(f"{drive_name}/", Qt.ItemDataRole.UserRole)
            folder = QStandardItem("Photos")
            folder.setData(f"{drive_name}/Photos", Qt.ItemDataRole.UserRole)
            folder.appendRow(QStandardItem("Child"))
            drive.appendRow(folder)
            other = QStandardItem("Other")
            other.appendRow(QStandardItem("Nested"))
            drive.appendRow(other)
            self.model.appendRow(drive)
        self.drive_c = self.model.index(0, 0)
        self.drive_d = self.model.index(1, 0)

    def test_opening_a_drive_collapses_the_previous_drive_by_default(self) -> None:
        self.tree.expand(self.drive_c)
        self.tree.expand(self.drive_d)

        self.assertFalse(self.tree.isExpanded(self.drive_c))
        self.assertTrue(self.tree.isExpanded(self.drive_d))

    def test_nested_folders_do_not_collapse_the_active_drive(self) -> None:
        folder = self.model.index(0, 0, self.drive_c)
        self.tree.expand(self.drive_c)
        self.tree.expand(folder)

        self.assertTrue(self.tree.isExpanded(self.drive_c))
        self.assertTrue(self.tree.isExpanded(folder))

    def test_disabling_the_setting_allows_multiple_open_drives(self) -> None:
        self.tree.set_single_drive_expansion_enabled(False)
        self.tree.expand(self.drive_c)
        self.tree.expand(self.drive_d)

        self.assertTrue(self.tree.isExpanded(self.drive_c))
        self.assertTrue(self.tree.isExpanded(self.drive_d))

    def test_opening_a_folder_collapses_its_expanded_sibling(self) -> None:
        photos = self.model.index(0, 0, self.drive_c)
        other = self.model.index(1, 0, self.drive_c)
        self.tree.expand(self.drive_c)
        self.tree.expand(photos)
        self.tree.expand(other)

        self.assertFalse(self.tree.isExpanded(photos))
        self.assertTrue(self.tree.isExpanded(other))

    def test_disabling_the_setting_allows_multiple_open_sibling_folders(self) -> None:
        photos = self.model.index(0, 0, self.drive_c)
        other = self.model.index(1, 0, self.drive_c)
        self.tree.set_single_drive_expansion_enabled(False)
        self.tree.expand(self.drive_c)
        self.tree.expand(photos)
        self.tree.expand(other)

        self.assertTrue(self.tree.isExpanded(photos))
        self.assertTrue(self.tree.isExpanded(other))

    def test_navigation_colors_can_follow_the_active_theme(self) -> None:
        selected = QColor(20, 80, 140, 60)
        hovered = QColor(30, 40, 50, 90)

        self.tree.set_navigation_colors(selected, hovered)

        self.assertEqual(selected, self.tree._selected_row_fill)
        self.assertEqual(hovered, self.tree._hovered_row_fill)

    def test_clicking_the_drive_row_expands_it_immediately(self) -> None:
        self.tree.resize(280, 180)
        self.tree.show()
        self.app.processEvents()

        QTest.mouseClick(
            self.tree.viewport(),
            Qt.MouseButton.LeftButton,
            pos=self.tree.visualRect(self.drive_c).center(),
        )

        self.assertTrue(self.tree.isExpanded(self.drive_c))

    def test_clicking_the_expanded_drive_row_collapses_it(self) -> None:
        self.tree.resize(280, 180)
        self.tree.show()
        self.tree.expand(self.drive_c)
        self.app.processEvents()

        QTest.mouseClick(
            self.tree.viewport(),
            Qt.MouseButton.LeftButton,
            pos=self.tree.visualRect(self.drive_c).center(),
        )

        self.assertFalse(self.tree.isExpanded(self.drive_c))

    def test_clicking_a_folder_row_expands_it_immediately(self) -> None:
        folder = self.model.index(0, 0, self.drive_c)
        self.tree.resize(280, 180)
        self.tree.show()
        self.tree.expand(self.drive_c)
        self.app.processEvents()

        QTest.mouseClick(
            self.tree.viewport(),
            Qt.MouseButton.LeftButton,
            pos=self.tree.visualRect(folder).center(),
        )

        self.assertEqual(self.tree.currentIndex(), folder)
        self.assertTrue(self.tree.isExpanded(folder))

    def test_clicking_an_expanded_folder_row_collapses_it(self) -> None:
        folder = self.model.index(0, 0, self.drive_c)
        self.tree.resize(280, 180)
        self.tree.show()
        self.tree.expand(self.drive_c)
        self.tree.expand(folder)
        self.app.processEvents()

        QTest.mouseClick(
            self.tree.viewport(),
            Qt.MouseButton.LeftButton,
            pos=self.tree.visualRect(folder).center(),
        )

        self.assertFalse(self.tree.isExpanded(folder))



if __name__ == "__main__":
    unittest.main()
