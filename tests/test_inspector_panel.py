from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractAnimation, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from image_triage.metadata import CaptureMetadata
from image_triage.review_tools import EMPTY_INSPECTION_STATS, InspectionStats
from image_triage.ui.docks import (
    INSPECTOR_PREVIEW_CHROME_HEIGHT,
    INSPECTOR_PREVIEW_COLLAPSED_HEIGHT,
    INSPECTOR_PREVIEW_IMAGE_RATIO,
    INSPECTOR_SECTION_HEADER_HEIGHT,
    InspectorPanel,
    InspectorPropertyRow,
    InspectorSeverity,
    build_workspace_docks,
)
from image_triage.ui.display_metrics import COMPACT_DISPLAY


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


def _preview_height(width: int) -> int:
    return max(120, round(width * INSPECTOR_PREVIEW_IMAGE_RATIO) + INSPECTOR_PREVIEW_CHROME_HEIGHT)


def _fill_capture(panel: InspectorPanel, value: str | None = None) -> None:
    values = {
        "Camera": "Nikon Z 7II",
        "Lens": "NIKKOR Z 14-24mm f/2.8 S",
        "Settings": "1/320s · f/5.6 · ISO 800 · 24mm",
        "Pixels": "5,408 × 3,600 · 19.5 MP",
    }
    for name, default in values.items():
        panel.capture_rows[name].set_value(value if value is not None else default)


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

    def test_compact_profile_resizes_inspector_chrome_and_rows(self) -> None:
        panel = InspectorPanel()
        panel.resize(COMPACT_DISPLAY.inspector_width, 820)

        panel.apply_display_profile(COMPACT_DISPLAY)

        self.assertEqual(78, panel.culling_rows["Decision"].label.width())
        self.assertEqual(
            max(26, round(INSPECTOR_SECTION_HEADER_HEIGHT * COMPACT_DISPLAY.scale)),
            panel.histogram_section.header.height(),
        )
        self.assertEqual(COMPACT_DISPLAY.inspector_header_button_width, panel.close_button.width())
        self.assertEqual(COMPACT_DISPLAY.inspector_histogram_max_height, panel.histogram_widget.maximumHeight())

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

    def test_culling_reads_decision_rating_and_group(self) -> None:
        panel = InspectorPanel()

        self.assertEqual(tuple(panel.culling_rows), ("Decision", "Rating", "Group"))
        self.assertEqual(panel.culling_rows["Decision"].value_label.property("emphasis"), "strong")
        self.assertEqual(panel.culling_rows["Rating"].text(), "Unrated")
        self.assertEqual(panel.culling_rows["Group"].text(), "Single photo")

    def test_capture_rows_summarise_the_exif(self) -> None:
        metadata = CaptureMetadata(
            path="x.jpg",
            exposure="1/320s",
            aperture="f/5.6",
            iso="800",
            focal_length="24mm",
            width=5408,
            height=3600,
        )

        self.assertEqual(
            InspectorPanel._capture_settings_text(metadata),
            "1/320s · f/5.6 · ISO 800 · 24mm",
        )
        self.assertEqual(
            InspectorPanel._pixels_text(metadata, EMPTY_INSPECTION_STATS),
            "5,408 × 3,600 · 19.5 MP",
        )
        self.assertEqual(InspectorPanel._capture_settings_text(None), "")

    def test_preview_position_shows_and_bounds_navigation(self) -> None:
        panel = InspectorPanel()

        panel.set_position(1, 48)
        self.assertEqual(panel.preview_position.text(), "1 / 48")
        self.assertFalse(panel.preview_previous_button.isEnabled())
        self.assertTrue(panel.preview_next_button.isEnabled())

        panel.set_position(0, 0)
        self.assertEqual(panel.preview_position.text(), "")
        self.assertTrue(panel.preview_next_button.isHidden())

    def test_ai_analysis_invites_analysis_until_results_exist(self) -> None:
        panel = InspectorPanel()

        self.assertEqual(panel.ai_stack.currentIndex(), 0)
        self.assertEqual(panel.ai_section.aside_label.text(), "Not analyzed")
        requests: list[bool] = []
        panel.analyze_requested.connect(lambda: requests.append(True))
        panel.analyze_button.click()
        self.assertEqual(requests, [True])

        panel._set_ai_analyzed(True)
        self.assertEqual(panel.ai_stack.currentIndex(), 1)
        self.assertTrue(panel.ai_section.aside_label.isHidden())

    def test_histogram_exposure_warning_uses_default_summary_style(self) -> None:
        panel = InspectorPanel()

        panel._set_histogram_summary(_underexposed_stats())
        self.assertTrue(panel.histogram_summary.text().startswith("Underexposed:"))
        self.assertEqual(panel.histogram_summary.objectName(), "inspectorHint")

        panel._set_histogram_summary(EMPTY_INSPECTION_STATS)
        self.assertEqual(panel.histogram_summary.text(), "Not analyzed")

    def test_histogram_height_stays_stable_when_its_summary_changes(self) -> None:
        host = QWidget()
        host.setFixedSize(370, 900)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)
        host.show()
        self.app.processEvents()
        initial_height = panel.histogram_section.height()

        panel.preview_collapse_button.setChecked(False)
        panel._set_histogram_summary(_underexposed_stats())
        panel._sync_inspector_geometry()
        self.app.processEvents()

        self.assertEqual(panel.histogram_section.height(), initial_height)
        self.assertEqual(panel.histogram_section.height(), panel.SECTION_HEIGHTS["histogram"])
        host.close()

    def test_full_section_header_toggles_body(self) -> None:
        panel = InspectorPanel()
        panel.resize(320, 900)
        panel.show()
        self.app.processEvents()
        section = panel._sections["capture"]

        QTest.mouseClick(section.header, Qt.MouseButton.LeftButton, pos=QPoint(40, 15))

        self.assertFalse(section.is_expanded())
        self.assertTrue(section.body.isHidden())
        panel.close()

    def test_section_header_uses_a_sentence_case_title_after_its_chevron(self) -> None:
        panel = InspectorPanel()
        panel.resize(320, 1250)
        panel.show()
        self.app.processEvents()
        section = panel._sections["capture"]
        title_left = section.header.title.mapTo(section, QPoint(0, 0)).x()
        chevron_left = section.header.chevron.mapTo(section, QPoint(0, 0)).x()

        self.assertEqual(section.header.geometry().left(), 0)
        self.assertEqual(section.header.width(), section.width())
        self.assertLess(chevron_left, title_left)
        self.assertEqual(section.header.title.text(), "Capture")
        panel.close()

    def test_first_property_row_has_the_same_top_inset_in_each_section(self) -> None:
        panel = InspectorPanel()
        panel.resize(320, 1250)
        _fill_capture(panel)
        panel.culling_rows["Group"].set_value("Burst · 2 of 4")
        panel.show()
        self.app.processEvents()
        panel._sync_inspector_geometry()
        self.app.processEvents()

        first_rows = (
            (panel._sections["culling"], panel.culling_rows["Decision"]),
            (panel._sections["capture"], panel.capture_rows["Camera"]),
        )
        insets = {
            row.value_label.mapTo(section, QPoint(0, 0)).y()
            - section.header.geometry().bottom()
            - 1
            for section, row in first_rows
        }

        self.assertEqual(len(insets), 1)
        self.assertEqual(insets, {5})
        panel.close()

    def test_section_heights_do_not_change_with_photo_content(self) -> None:
        panel = InspectorPanel()
        panel.resize(320, 1250)
        panel.show()
        self.app.processEvents()
        initial_heights = {
            key: section.height() for key, section in panel._sections.items()
        }

        panel.culling_rows["Group"].set_value("Burst · 2 of 4")
        _fill_capture(panel)
        panel._sync_inspector_geometry()
        self.app.processEvents()

        self.assertEqual(
            {key: section.height() for key, section in panel._sections.items()},
            initial_heights,
        )
        self.assertTrue(panel._sections["ai_analysis"].property("lastInspectorSection"))

        panel.close()

    def test_ui_state_round_trip_covers_every_section(self) -> None:
        panel = InspectorPanel()
        panel.preview_collapse_button.setChecked(False)
        panel._sections["capture"].set_expanded(False)

        state = panel.save_ui_state()
        self.assertEqual(set(state["sections"]), set(panel.SECTION_KEYS))

        restored = InspectorPanel()
        self.assertTrue(restored.restore_ui_state(state))
        self.assertFalse(restored.preview_collapse_button.isChecked())
        self.assertFalse(restored._sections["capture"].is_expanded())
        self.assertTrue(restored._sections["ai_analysis"].is_expanded())

        stale_auto_collapse_state = {
            "version": 1,
            "sections": {key: False for key in panel.SECTION_KEYS},
        }
        self.assertTrue(restored.restore_ui_state(stale_auto_collapse_state))
        self.assertTrue(restored.preview_collapse_button.isChecked())
        self.assertTrue(all(section.is_expanded() for section in restored._sections.values()))

    def test_panel_uses_one_details_scroller_beneath_the_pinned_preview(self) -> None:
        host = QWidget()
        host.setFixedSize(320, 1250)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)
        host.show()
        self.app.processEvents()

        self.assertEqual(panel.height(), host.height())
        self.assertEqual(panel.preview_card.height(), _preview_height(panel.preview_card.width()))
        self.assertEqual(panel.culling_rows["Decision"].label.width(), 96)
        self.assertEqual(panel.details_scroll.objectName(), "inspectorScrollArea")
        self.assertEqual(panel.details_scroll.geometry().top(), panel.preview_card.geometry().bottom() + 7)
        self.assertEqual(panel.details_scroll.geometry().bottom(), panel.height() - 1)
        self.assertEqual(panel.findChildren(QScrollArea, "inspectorSectionScrollArea"), [])
        self.assertTrue(all(section.body_scroll is None for section in panel._sections.values()))
        self.assertFalse(panel.details_scroll.verticalScrollBar().isVisible())

        preview_height = panel.preview_card.height()
        host.setFixedHeight(1350)
        self.app.processEvents()
        self.assertEqual(panel.preview_card.height(), preview_height)
        host.close()

    def test_static_section_stack_does_not_scroll_when_total_height_fits(self) -> None:
        host = QWidget()
        host.setFixedSize(324, 1400)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)
        panel.histogram_summary.setText(
            "Underexposed: data is pressed against the left edge; "
            "shadow detail may be clipped."
        )
        _fill_capture(panel)

        host.show()
        self.app.processEvents()
        panel._sync_inspector_geometry()
        self.app.processEvents()

        sections = tuple(panel._sections.values())
        stack_height = sum(section.height() for section in sections)
        exact_fit_height = (
            panel.preview_card.height()
            + panel._inspector_pinned_layout.spacing()
            + stack_height
        )
        host.setFixedHeight(exact_fit_height)
        self.app.processEvents()
        panel._sync_inspector_geometry()
        self.app.processEvents()

        self.assertGreaterEqual(panel.details_scroll.viewport().height(), stack_height)
        self.assertFalse(panel.details_scroll.verticalScrollBar().isVisible())
        self.assertTrue(all(section.body_scroll is None for section in sections))
        host.close()

    def test_preview_card_keeps_its_landscape_frame_at_supported_panel_widths(self) -> None:
        for width in (276, 300, 320, 460):
            with self.subTest(width=width):
                host = QWidget()
                host.setFixedSize(width, width + 1200)
                host_layout = QVBoxLayout(host)
                host_layout.setContentsMargins(0, 0, 0, 0)
                panel = InspectorPanel()
                host_layout.addWidget(panel)
                host.show()
                self.app.processEvents()
                panel._sync_inspector_geometry()
                self.app.processEvents()

                self.assertEqual(panel.preview_card.height(), _preview_height(panel.preview_card.width()))
                host.close()

    def test_long_inspector_values_expand_the_card_and_scroll_as_one_stack(self) -> None:
        host = QWidget()
        host.setFixedSize(320, 700)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)
        _fill_capture(
            panel,
            "A long inspection explanation that wraps inside this card and remains "
            "available by scrolling without moving the rest of the inspector. " * 6,
        )

        host.show()
        self.app.processEvents()
        self.app.processEvents()
        section = panel._sections["capture"]
        bar = panel.details_scroll.verticalScrollBar()

        self.assertGreater(bar.maximum(), 0)
        self.assertTrue(bar.isVisible())
        self.assertEqual(section.height(), panel.SECTION_HEIGHTS["capture"])
        self.assertTrue(all(candidate.body_scroll is None for candidate in panel._sections.values()))
        host.close()

    def test_inspector_stack_wheel_scrolling_is_animated_and_accumulates(self) -> None:
        host = QWidget()
        host.setFixedSize(320, 600)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)
        _fill_capture(panel, "A long inspector value that must scroll smoothly. " * 24)

        host.show()
        self.app.processEvents()
        scroll = panel.details_scroll
        bar = scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)

        def wheel_down() -> QWheelEvent:
            return QWheelEvent(
                QPointF(10, 10),
                QPointF(10, 10),
                QPoint(),
                QPoint(0, -120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.ScrollUpdate,
                False,
            )

        first = wheel_down()
        scroll.wheelEvent(first)
        first_target = int(scroll._scroll_animation.endValue())
        self.assertTrue(first.isAccepted())
        self.assertEqual(QAbstractAnimation.State.Running, scroll._scroll_animation.state())
        self.assertGreater(first_target, bar.value())

        second = wheel_down()
        scroll.wheelEvent(second)
        second_target = int(scroll._scroll_animation.endValue())
        self.assertGreater(second_target, first_target)

        scroll._scroll_animation.setCurrentTime(scroll._scroll_animation.duration())
        self.assertEqual(second_target, bar.value())
        host.close()

    def test_collapsed_card_shortens_stack_without_resizing_other_cards(self) -> None:
        host = QWidget()
        host.setFixedSize(320, 600)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)
        _fill_capture(panel, "A long capture explanation that needs additional vertical space. " * 24)

        host.show()
        self.app.processEvents()
        panel._sync_inspector_geometry()
        self.app.processEvents()
        capture = panel._sections["capture"]
        original_capture_height = capture.height()
        original_histogram_height = panel.histogram_section.height()
        original_scroll_maximum = panel.details_scroll.verticalScrollBar().maximum()

        panel._sections["culling"].set_expanded(False)
        self.app.processEvents()

        self.assertEqual(capture.height(), original_capture_height)
        self.assertEqual(panel.histogram_section.height(), original_histogram_height)
        self.assertEqual(
            panel.histogram_section.height(),
            panel.SECTION_HEIGHTS["histogram"],
        )
        self.assertLess(panel.details_scroll.verticalScrollBar().maximum(), original_scroll_maximum)
        host.close()

    def test_collapsed_preview_releases_its_full_height_to_open_sections(self) -> None:
        host = QWidget()
        host.setFixedSize(400, 760)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)
        host.show()
        self.app.processEvents()

        panel.preview_collapse_button.setChecked(False)
        panel._sections["culling"].set_expanded(False)
        panel._sections["capture"].set_expanded(False)
        panel._sync_inspector_geometry()
        self.app.processEvents()

        self.assertEqual(INSPECTOR_PREVIEW_COLLAPSED_HEIGHT, panel.preview_card.height())
        for key in ("histogram", "ai_analysis"):
            section = panel._sections[key]
            self.assertEqual(section.height(), panel.SECTION_HEIGHTS[key])
            self.assertIsNone(section.body_scroll)
        self.assertFalse(panel.details_scroll.verticalScrollBar().isVisible())
        host.close()

    def test_warning_heavy_content_does_not_clip_the_preview_or_last_section(self) -> None:
        host = QWidget()
        host.setFixedSize(300, 1250)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        host_layout.addWidget(panel)

        panel._set_histogram_summary(_underexposed_stats())
        panel.culling_rows["Group"].set_value("Burst · 2 of 4")
        _fill_capture(panel)

        host.show()
        self.app.processEvents()
        panel._sync_preview_card_aspect()
        self.app.processEvents()

        self.assertEqual(panel.preview_card.height(), _preview_height(panel.preview_card.width()))
        self.assertEqual(panel.culling_rows["Decision"].label.width(), 82)
        self.assertTrue(all(section.is_expanded() for section in panel._sections.values()))
        self.assertEqual(panel.details_scroll.geometry().top(), panel.preview_card.geometry().bottom() + 7)
        previous_bottom = -1
        for section in panel._sections.values():
            self.assertEqual(section.geometry().top(), previous_bottom + 1)
            self.assertEqual(section.height(), panel.SECTION_HEIGHTS[section.key])
            self.assertIsNone(section.body_scroll)
            self.assertLessEqual(section.body.geometry().bottom(), section.height() - 1)
            previous_bottom = section.geometry().bottom()
        self.assertLessEqual(previous_bottom, panel.details_body.height() - 1)
        host.close()

    def test_context_updates_do_not_auto_collapse_or_reorder_sections(self) -> None:
        host = QWidget()
        host.setFixedSize(320, 1250)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        panel = InspectorPanel()
        panel.culling_rows["Group"].set_value("Burst · 1 of 4")
        panel._set_ai_analyzed(True)
        host_layout.addWidget(panel)
        host.show()
        self.app.processEvents()

        self.assertTrue(all(section.is_expanded() for section in panel._sections.values()))
        self.assertEqual(tuple(panel._sections), ("histogram", "culling", "capture", "ai_analysis"))
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
        panel._sections["capture"].set_expanded(False)
        self.app.processEvents()

        for key, section in panel._sections.items():
            expected = section.header.height() if key == "capture" else original_heights[key]
            self.assertEqual(section.height(), expected)
        ordered = list(panel._sections.values())
        for previous, current in zip(ordered, ordered[1:]):
            self.assertEqual(current.geometry().top(), previous.geometry().bottom() + 1)
        self.assertLess(ordered[-1].geometry().bottom(), panel.height() - 1)

        panel.preview_collapse_button.setChecked(False)
        for section in ordered:
            section.set_expanded(False)
        self.app.processEvents()
        self.assertEqual(panel.preview_card.height(), INSPECTOR_PREVIEW_COLLAPSED_HEIGHT)
        self.assertTrue(all(section.height() == section.header.height() for section in ordered))
        self.assertEqual(ordered[0].geometry().top(), 0)
        self.assertEqual(panel.details_scroll.geometry().top(), panel.preview_card.geometry().bottom() + 7)
        host.close()

    def test_context_menu_controls_sections_preview_and_pane_visibility(self) -> None:
        panel = InspectorPanel()
        target = panel._sections["capture"]
        menu = panel._build_context_menu(target)
        actions = {action.text(): action for action in menu.actions() if action.text()}

        self.assertEqual(
            set(actions),
            {
                "Expand All Sections",
                "Collapse All Sections",
                "Collapse Other Sections",
                "Show Preview",
                "Pop Out Inspector",
                "Swap Panel Sides",
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
        inspector._sections["capture"].set_expanded(False)

        state = docks.save_state()
        self.assertEqual(state["version"], 4)
        self.assertIn("content_state", state["panels"]["inspector"])

        inspector.reset_ui_state()
        self.assertTrue(docks.restore_state(state))
        self.assertFalse(inspector._sections["capture"].is_expanded())

        legacy = dict(state)
        legacy["version"] = 3
        legacy["panels"] = {key: dict(value) for key, value in state["panels"].items()}
        legacy["panels"]["inspector"].pop("content_state", None)
        self.assertTrue(docks.restore_state(legacy))
        self.assertTrue(all(section.is_expanded() for section in inspector._sections.values()))
        self.assertTrue(inspector.preview_collapse_button.isChecked())


if __name__ == "__main__":
    unittest.main()
