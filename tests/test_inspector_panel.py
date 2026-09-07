from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from image_triage.review_tools import EMPTY_INSPECTION_STATS, InspectionStats
from image_triage.ui.docks import (
    INSPECTOR_PREVIEW_COLLAPSED_HEIGHT,
    InspectorPanel,
    InspectorPropertyRow,
    InspectorSeverity,
    build_workspace_docks,
)


def _underexposed_stats() -> InspectionStats:
    histogram = tuple(100 if index < 8 else 0 for index in range(256))
    return InspectionStats(
        width=100,
        height=100,
        mean_luminance=4.0,
        median_luminance=3.0,
        shadow_clip_pct=5.0,
        highlight_clip_pct=0.0,
        detail_score=50.0,
        histogram_luma=histogram,
        histogram_red=histogram,
        histogram_green=histogram,
        histogram_blue=histogram,
    )


class InspectorPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_property_row_uses_fixed_label_column_and_semantic_state(self) -> None:
        row = InspectorPropertyRow("Exposure")

        self.assertEqual(row.label.width(), 96)
        self.assertEqual(row.layout().spacing(), 6)
        row.set_value("Overexposed", severity=InspectorSeverity.WARNING)
        self.assertEqual(row.text(), "Overexposed")
        self.assertEqual(row.severity, InspectorSeverity.WARNING)
        self.assertFalse(row.warning_icon.isHidden())
        self.assertEqual(row.value_label.property("severity"), "warning")

    def test_severity_mapping_is_limited_to_actionable_conditions(self) -> None:
        warning_cases = (
            ("Exposure", "Underexposed"),
            ("Exposure", "Overexposed"),
            ("Focus", "Blur detected"),
            ("Motion Blur", "Possible"),
            ("Noise", "High"),
            ("Confidence", "Low"),
            ("Confidence", "22%"),
        )
        for row_name, value in warning_cases:
            with self.subTest(row_name=row_name, value=value):
                self.assertEqual(
                    InspectorPanel._severity_for_value(row_name, value),
                    InspectorSeverity.WARNING,
                )

        self.assertEqual(
            InspectorPanel._severity_for_value("Detail", "Processing failed"),
            InspectorSeverity.CRITICAL,
        )
        self.assertEqual(
            InspectorPanel._severity_for_value("Noise", "Moderate"),
            InspectorSeverity.NORMAL,
        )
        self.assertEqual(
            InspectorPanel._severity_for_value("Confidence", "Not analyzed"),
            InspectorSeverity.MUTED,
        )

    def test_manual_decision_is_stronger_than_ai_suggestion(self) -> None:
        panel = InspectorPanel()

        self.assertEqual(panel.culling_rows["Decision"].value_label.property("emphasis"), "strong")
        self.assertEqual(panel.culling_rows["AI Suggestion"].value_label.property("emphasis"), "secondary")
        self.assertEqual(panel.culling_rows["AI Suggestion"].text(), "No AI result")

    def test_histogram_exposure_warning_uses_default_summary_style(self) -> None:
        panel = InspectorPanel()

        panel._set_histogram_summary(_underexposed_stats())
        self.assertTrue(panel.histogram_summary.text().startswith("Underexposed:"))
        self.assertEqual(panel.histogram_summary.objectName(), "inspectorHint")

        panel._set_histogram_summary(EMPTY_INSPECTION_STATS)
        self.assertEqual(panel.histogram_summary.text(), "Not analyzed")

    def test_empty_sections_keep_original_row_layout(self) -> None:
        panel = InspectorPanel()

        group = panel._sections["group_comparison"]
        edit = panel._sections["edit_potential"]
        self.assertEqual(panel.group_rows["Group Size"].text(), "No similar images detected")
        self.assertEqual(panel.edit_rows["Worth Editing"].text(), "Not analyzed")
        self.assertFalse(group.body.isHidden())
        self.assertFalse(edit.body.isHidden())

    def test_subject_fallback_is_short_and_keeps_explanation_in_tooltip(self) -> None:
        panel = InspectorPanel()

        panel._set_subject_context(
            category_profile="uncategorized",
            category_info={},
            face_records=(),
            ai_result=None,
        )

        signal = panel.subject_rows["Signal"]
        self.assertEqual(signal.text(), "General guidance")
        self.assertEqual(signal.toolTip(), "No specialized category context available.")

    def test_full_section_header_toggles_body(self) -> None:
        panel = InspectorPanel()
        panel.resize(320, 900)
        panel.show()
        self.app.processEvents()
        section = panel._sections["quality"]

        QTest.mouseClick(section.header, Qt.MouseButton.LeftButton, pos=QPoint(40, 15))

        self.assertFalse(section.is_expanded())
        self.assertTrue(section.body.isHidden())
        panel.close()

    def test_section_title_aligns_with_rows_and_chevron_sits_on_right(self) -> None:
        panel = InspectorPanel()
        panel.resize(320, 1250)
        panel.show()
        self.app.processEvents()
        section = panel._sections["quality"]
        row = panel.quality_rows["Detail"]

        title_left = section.header.title.mapTo(section, QPoint(0, 0)).x()
        row_left = row.label.mapTo(section, QPoint(0, 0)).x()
        chevron_left = section.header.chevron.mapTo(section, QPoint(0, 0)).x()

        self.assertEqual(title_left, row_left)
        self.assertGreater(chevron_left, section.header.title.geometry().right())
        panel.close()

    def test_ui_state_round_trip_covers_all_seven_sections(self) -> None:
        panel = InspectorPanel()
        panel.preview_collapse_button.setChecked(False)
        panel._sections["quality"].set_expanded(False)

        state = panel.save_ui_state()
        self.assertEqual(set(state["sections"]), set(panel.SECTION_KEYS))

        restored = InspectorPanel()
        self.assertTrue(restored.restore_ui_state(state))
        self.assertFalse(restored.preview_collapse_button.isChecked())
        self.assertFalse(restored._sections["quality"].is_expanded())
        self.assertTrue(restored._sections["subject"].is_expanded())

        stale_auto_collapse_state = {
            "version": 1,
            "sections": {key: False for key in panel.SECTION_KEYS},
        }
        self.assertTrue(restored.restore_ui_state(stale_auto_collapse_state))
        self.assertTrue(restored.preview_collapse_button.isChecked())
        self.assertTrue(all(section.is_expanded() for section in restored._sections.values()))

    def test_panel_fills_available_height_without_a_scroll_container(self) -> None:
        host = QWidget()
        host.setFixedSize(320, 1250)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)
        host.show()
        self.app.processEvents()

        last_section = panel._sections["edit_potential"]
        self.assertEqual(panel.height(), host.height())
        self.assertEqual(last_section.geometry().bottom(), panel.height() - 1)
        self.assertEqual(panel.preview_card.width(), panel.preview_card.height())
        self.assertEqual(panel.culling_rows["Decision"].label.width(), 96)
        self.assertEqual(panel.findChildren(QScrollArea), [])

        square_side = panel.preview_card.height()
        host.setFixedHeight(1350)
        self.app.processEvents()
        self.assertEqual(panel.preview_card.width(), square_side)
        self.assertEqual(panel.preview_card.height(), square_side)
        host.close()

    def test_warning_heavy_content_does_not_clip_the_square_preview_or_last_section(self) -> None:
        host = QWidget()
        host.setFixedSize(300, 1250)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)

        panel._set_histogram_summary(_underexposed_stats())
        panel.culling_rows["Reason"].set_value(
            "Score lands near the bottom of the folder. Demoted because a stronger frame "
            "in the same burst already passes as Winner."
        )
        panel.subject_rows["Signal"].set_value("No specialized category context available.")
        panel.group_rows["Why"].set_value(
            "Burst specialist currently prefers another frame in this similar group."
        )
        panel.edit_rows["Notes"].set_value("Similar 2/3")

        host.show()
        self.app.processEvents()
        panel._sync_preview_card_aspect()
        self.app.processEvents()

        self.assertEqual(panel.preview_card.width(), panel.preview_card.height())
        self.assertEqual(panel.culling_rows["Decision"].label.width(), 82)
        self.assertTrue(all(section.is_expanded() for section in panel._sections.values()))
        self.assertEqual(panel._sections["edit_potential"].geometry().bottom(), panel.height() - 1)
        previous_bottom = panel.preview_card.geometry().bottom()
        for section in panel._sections.values():
            self.assertGreater(section.geometry().top(), previous_bottom)
            self.assertGreaterEqual(section.height(), section.minimumSizeHint().height())
            previous_bottom = section.geometry().bottom()
        host.close()

    def test_context_updates_do_not_auto_collapse_or_reorder_sections(self) -> None:
        host = QWidget()
        host.setFixedSize(320, 1250)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        panel.culling_rows["Reason"].set_value(
            "Strong expression and the sharpest frame in the comparison group."
        )
        panel._sections["group_comparison"].set_empty_state(None)
        for name, value in {
            "Group Size": "4 images",
            "Rank": "1 of 4",
            "Best Candidate": "Yes",
            "Similar Files": "3",
            "Duplicate Risk": "Low",
            "Why": "Best focus and expression",
        }.items():
            panel.group_rows[name].set_value(value)
        host_layout.addWidget(panel)
        host.show()
        self.app.processEvents()

        self.assertTrue(all(section.is_expanded() for section in panel._sections.values()))
        self.assertEqual(tuple(panel._sections), (
            "histogram",
            "culling",
            "subject",
            "quality",
            "group_comparison",
            "edit_potential",
        ))
        host.close()

    def test_collapsed_sections_stack_without_stretching_open_sections(self) -> None:
        host = QWidget()
        host.setFixedSize(320, 1250)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)
        host.show()
        self.app.processEvents()

        original_heights = {
            key: section.height() for key, section in panel._sections.items()
        }
        panel._sections["subject"].set_expanded(False)
        self.app.processEvents()

        for key, section in panel._sections.items():
            expected = 34 if key == "subject" else original_heights[key]
            self.assertEqual(section.height(), expected)
        ordered = list(panel._sections.values())
        for previous, current in zip(ordered, ordered[1:]):
            self.assertEqual(current.geometry().top(), previous.geometry().bottom() + 7)
        self.assertLess(ordered[-1].geometry().bottom(), panel.height() - 1)

        panel.preview_collapse_button.setChecked(False)
        for section in ordered:
            section.set_expanded(False)
        self.app.processEvents()
        self.assertEqual(panel.preview_card.height(), INSPECTOR_PREVIEW_COLLAPSED_HEIGHT)
        self.assertTrue(all(section.height() == 34 for section in ordered))
        self.assertEqual(ordered[0].geometry().top(), panel.preview_card.geometry().bottom() + 7)
        host.close()

    def test_context_menu_controls_sections_preview_and_pane_visibility(self) -> None:
        panel = InspectorPanel()
        target = panel._sections["quality"]
        menu = panel._build_context_menu(target)
        actions = {action.text(): action for action in menu.actions() if action.text()}

        self.assertEqual(
            set(actions),
            {
                "Expand All Sections",
                "Collapse All Sections",
                "Collapse Other Sections",
                "Show Preview",
                "Hide Inspector Pane",
            },
        )
        self.assertTrue(actions["Show Preview"].isChecked())

        actions["Collapse All Sections"].trigger()
        self.assertFalse(panel.preview_collapse_button.isChecked())
        self.assertTrue(all(not section.is_expanded() for section in panel._sections.values()))

        actions["Expand All Sections"].trigger()
        self.assertTrue(panel.preview_collapse_button.isChecked())
        self.assertTrue(all(section.is_expanded() for section in panel._sections.values()))

        actions["Collapse Other Sections"].trigger()
        self.assertTrue(target.is_expanded())
        self.assertTrue(
            all(not section.is_expanded() for section in panel._sections.values() if section is not target)
        )

        close_requests: list[bool] = []
        panel.close_requested.connect(lambda: close_requests.append(True))
        actions["Hide Inspector Pane"].trigger()
        self.assertEqual(close_requests, [True])

    def test_workspace_state_v4_round_trip_and_v3_defaults(self) -> None:
        shell_parent = QWidget()
        inspector = InspectorPanel()
        docks = build_workspace_docks(shell_parent, QWidget(), inspector, QWidget())
        inspector._sections["quality"].set_expanded(False)

        state = docks.save_state()
        self.assertEqual(state["version"], 4)
        self.assertIn("content_state", state["panels"]["inspector"])

        inspector.reset_ui_state()
        self.assertTrue(docks.restore_state(state))
        self.assertFalse(inspector._sections["quality"].is_expanded())

        legacy = dict(state)
        legacy["version"] = 3
        legacy["panels"] = {key: dict(value) for key, value in state["panels"].items()}
        legacy["panels"]["inspector"].pop("content_state", None)
        self.assertTrue(docks.restore_state(legacy))
        self.assertTrue(all(section.is_expanded() for section in inspector._sections.values()))
        self.assertTrue(inspector.preview_collapse_button.isChecked())


if __name__ == "__main__":
    unittest.main()
