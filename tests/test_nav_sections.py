"""The left-nav sections keep a fixed order, whatever is collapsed.

Collapsing used to sink a header to the bottom of the pane, which read as the
two sections swapping places. These pin the order down.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QListWidget, QVBoxLayout, QWidget

from image_triage.ui.face_groups import FaceGroupsPanel
from image_triage.ui.sections import SectionHeader
from image_triage.window import MainWindow

SPACE = "<space>"


class _NavHost:
    """Only the pieces of MainWindow the section layout touches."""

    def __init__(self) -> None:
        self.left_nav_body = QWidget()
        self._nav_layout = QVBoxLayout(self.left_nav_body)
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        self.face_groups_header = SectionHeader("Face Groups")
        self.face_groups_panel = FaceGroupsPanel()
        self.projects_header = SectionHeader("Projects")
        self.projects_list = QListWidget()

    _nav_sections = MainWindow._nav_sections
    _relayout_nav_sections = MainWindow._relayout_nav_sections


class NavSectionOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.host = _NavHost()

    def tearDown(self) -> None:
        self.host.face_groups_panel.shutdown()

    def _arrange(self, *, faces: bool, projects: bool) -> list[str]:
        self.host.face_groups_header.set_expanded(faces)
        self.host.projects_header.set_expanded(projects)
        self.host._relayout_nav_sections()
        layout = self.host._nav_layout
        names = []
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is None:
                names.append(SPACE)
            elif isinstance(widget, SectionHeader):
                names.append(widget.title.text())
            else:
                names.append(type(widget).__name__)
        return names

    def test_face_groups_leads_in_every_combination(self) -> None:
        for faces in (True, False):
            for projects in (True, False):
                with self.subTest(faces=faces, projects=projects):
                    order = self._arrange(faces=faces, projects=projects)
                    self.assertEqual("Face Groups", order[0])
                    self.assertLess(order.index("Face Groups"), order.index("Projects"))

    def test_the_free_space_is_always_at_the_bottom(self) -> None:
        for faces in (True, False):
            for projects in (True, False):
                with self.subTest(faces=faces, projects=projects):
                    self.assertEqual(SPACE, self._arrange(faces=faces, projects=projects)[-1])

    def test_an_expanded_body_sits_under_its_own_header(self) -> None:
        self.assertEqual(
            ["Face Groups", "FaceGroupsPanel", "Projects", "QListWidget", SPACE],
            self._arrange(faces=True, projects=True),
        )

    def test_a_collapsed_section_shrinks_to_its_header_in_place(self) -> None:
        order = self._arrange(faces=False, projects=True)
        self.assertEqual(["Face Groups", "Projects", "QListWidget", SPACE], order)
        self.assertTrue(self.host.face_groups_panel.isHidden())
        self.assertFalse(self.host.projects_list.isHidden())

    def test_headers_stay_reachable_across_repeated_toggles(self) -> None:
        # The rebuild reparents every widget, which hides it; a header that was
        # not shown again would leave its section unreachable.
        for _ in range(3):
            self._arrange(faces=False, projects=False)
            self._arrange(faces=True, projects=True)
        self.assertFalse(self.host.face_groups_header.isHidden())
        self.assertFalse(self.host.projects_header.isHidden())
        self.assertFalse(self.host.face_groups_panel.isHidden())


if __name__ == "__main__":
    unittest.main()
