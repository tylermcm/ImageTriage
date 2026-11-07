from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMainWindow

from image_triage.models import ImageRecord, SortMode, sort_records


class _GridState:
    def visible_item_count(self) -> int:
        return 4

    def selected_count(self) -> int:
        return 1

    def select_all(self) -> None:
        pass

    def clear_selection(self, *, keep_current: bool = True) -> None:
        pass


class WorkspaceContextMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _action(parent, text: str, *, checkable: bool = False) -> QAction:
        action = QAction(text, parent)
        action.setCheckable(checkable)
        return action


    def test_file_type_sort_uses_extension_then_filename(self) -> None:
        records = [
            ImageRecord(path="C:/photos/z.png", name="z.png", size=1, modified_ns=1),
            ImageRecord(path="C:/photos/b.jpg", name="b.jpg", size=1, modified_ns=1),
            ImageRecord(path="C:/photos/a.jpg", name="a.jpg", size=1, modified_ns=1),
        ]

        ordered = sort_records(records, SortMode.TYPE)

        self.assertEqual(["a.jpg", "b.jpg", "z.png"], [record.name for record in ordered])



if __name__ == "__main__":
    unittest.main()
