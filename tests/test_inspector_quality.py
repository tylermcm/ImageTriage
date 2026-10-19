"""Preview measurements produce useful findings before folder AI analysis."""
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from image_triage.models import ImageRecord
from image_triage.review_tools import EMPTY_INSPECTION_STATS
from image_triage.ui.docks import InspectorPanel
from image_triage.ui.inspector_quality import quality_readout


def measured(**changes):
    base = replace(EMPTY_INSPECTION_STATS, width=600, height=400,
                   median_luminance=120, detail_score=82, detail_valid_tile_count=8,
                   histogram_luma=tuple(100 if 90 <= i <= 150 else 0 for i in range(256)))
    return replace(base, **changes)


def test_no_measurements_does_not_claim_a_clean_photo():
    values, summary = quality_readout(EMPTY_INSPECTION_STATS)
    assert set(values.values()) == {"Pending"}
    assert "when the selected photo loads" in summary


def test_low_detail_noise_and_clipping_are_explained():
    values, summary = quality_readout(measured(detail_score=23, noise_score=78, highlight_clip_pct=5, motion_blur_score=75))
    assert values["Exposure"] == "Highlights clipped · 5.0%"
    assert values["Focus"] == "Low detail · 23/100"
    assert values["Noise"] == "High · 78/100"
    assert values["Motion"] == "Possible blur"
    assert "missed focus" in summary and "noise" in summary and "motion blur" in summary


def test_low_texture_is_inconclusive_not_out_of_focus():
    values, summary = quality_readout(measured(detail_score=2, detail_valid_tile_count=0))
    assert values["Focus"] == "Inconclusive"
    assert "Too few textured regions" in summary
    assert "missed focus" not in summary


def test_bright_and_dark_tones_without_clipping_are_not_called_bad_exposure():
    assert quality_readout(measured(median_luminance=210))[0]["Exposure"] == "Bright tones"
    assert quality_readout(measured(median_luminance=35))[0]["Exposure"] == "Dark tones"


def test_panel_updates_histogram_without_ai_and_clears_stale_values():
    app = QApplication.instance() or QApplication([])
    panel = InspectorPanel()
    record = ImageRecord(path="C:/Photos/test.jpg", name="test.jpg", size=2097152, modified_ns=0)
    panel.set_context(folder="C:/Photos", mode_label="All", selected_count=1,
                      current_record=record, display_path=record.path, annotation=None,
                      ai_result=None, inspection_stats=measured())
    assert "file" not in panel._sections
    assert panel.ai_stack.currentIndex() == 0
    assert "Sharp detail" in panel.quality_rows["Focus"].text()
    assert panel.histogram_widget._stats.width == 600
    panel.clear()
    assert panel.quality_rows["Focus"].text() == "Pending"
    panel.close()
    app.processEvents()


def test_analyze_stays_inside_card_and_keyboard_focus_survives():
    app = QApplication.instance() or QApplication([])
    panel = InspectorPanel()
    panel.resize(320, 1450)
    panel.show()
    app.processEvents()
    frame = panel.ai_section.body
    assert frame.objectName() == "inspectorAiPlaceholder"
    button = panel.analyze_button
    panel.set_ai_box_size(296, 600)
    app.processEvents()
    chips_bottom = max(chip.mapTo(frame, chip.rect().bottomRight()).y() for chip in panel._ai_chips)
    assert 0 <= button.mapTo(frame, button.rect().topLeft()).y() - chips_bottom <= 20
    point = button.mapTo(frame, button.rect().bottomRight())
    assert frame.rect().contains(point)
    button.setFocus(Qt.FocusReason.TabFocusReason)
    app.processEvents()
    assert button._keyboard_focus
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert not button._keyboard_focus
    panel.close()


def test_long_network_folder_does_not_widen_details_or_clip_sections():
    app = QApplication.instance() or QApplication([])
    panel = InspectorPanel()
    panel.resize(300, 1150)
    path = r"\\192.168.1.200\ColossalBoi\Photography\2026\VeryLongFolderNameWithoutAnySpaces\Photos"
    record = ImageRecord(path=path, name="Photos", size=0, modified_ns=0, is_folder=True)
    panel.set_context(folder=path, mode_label="All", selected_count=1,
                      current_record=record, display_path=path, annotation=None, ai_result=None)
    panel.set_ai_box_size(400, 180)
    panel.show()
    for _ in range(4):
        app.processEvents()
        panel._sync_inspector_geometry()
    viewport = panel.details_scroll.viewport()
    assert panel.details_body.width() <= viewport.width()
    for section in panel._sections.values():
        assert section.geometry().right() < viewport.width()
    panel.resize(260, 850)
    for _ in range(4):
        app.processEvents()
        panel._sync_inspector_geometry()
    assert panel.details_body.width() <= viewport.width()
    panel.close()
