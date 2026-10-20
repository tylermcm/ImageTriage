"""Apply AI Decisions says why each frame is a Reject, with plain words for the cascade's pass-1 flags."""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFilter
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel

from image_triage.ai_why import build_ai_why, flag_labels
from image_triage.models import ImageRecord
from image_triage.ui.apply_ai_decisions_dialog import ApplyAIDecisionsDialog
from tests.harness import pump_until
from tests.test_cascade_integration import run_analyze
from tests.test_cascade_run import record, save, scene


class StubThumbnails(QObject):
    thumbnail_ready = Signal(object, object)

    def get_cached(self, record, size):
        return None

    def request_thumbnail(self, record, size, **kwargs):
        return (record.path, size.width())


def rec(name: str) -> ImageRecord:
    return ImageRecord(path=f"C:/photos/{name}", name=name, size=1, modified_ns=1)


def test_the_cascade_flags_have_plain_labels():
    assert flag_labels("blink,soft,clipped,blown_out") == ("Eyes closed", "Softer than its siblings", "More blown highlights than its siblings", "Highlights blown out")
    assert flag_labels("blownout") == ("Blown highlights",)  # the older flags keep working


def test_the_dialog_shows_a_reason_under_each_frame_that_has_one():
    QApplication.instance() or QApplication([])
    a, b = rec("a.jpg"), rec("b.jpg")
    dialog = ApplyAIDecisionsDialog(ai_pick_records=[], reject_records=[a, b], keeper_count=0, review_count=0, thumbnail_manager=StubThumbnails(),
                                    reasons={a.path: "Eyes closed, Softer than its siblings"})
    try:
        labels = dialog.findChildren(QLabel, "applyDecisionReason")
        assert [label.toolTip() for label in labels] == ["Eyes closed, Softer than its siblings"]  # b has no reason, so no empty line
    finally:
        dialog.deleteLater()


def test_the_dialog_without_reasons_behaves_as_before():
    QApplication.instance() or QApplication([])
    dialog = ApplyAIDecisionsDialog(ai_pick_records=[], reject_records=[rec("a.jpg")], keeper_count=0, review_count=0, thumbnail_manager=StubThumbnails())
    try:
        assert dialog.reasons == {} and not dialog.findChildren(QLabel, "applyDecisionReason")
    finally:
        dialog.deleteLater()


def test_the_controller_builds_reasons_from_the_cascade_flags(main_window, tmp_path, monkeypatch):
    base = scene(1)
    save(tmp_path / "a1.jpg", base, 0)
    save(tmp_path / "a2.jpg", base.point(lambda v: min(255, v + 1)), 1)
    save(tmp_path / "a3_soft.jpg", base.filter(ImageFilter.GaussianBlur(5)), 2)
    run_analyze(main_window, monkeypatch, tmp_path, count=3)
    soft = next(r for r in main_window._all_records if r.name == "a3_soft.jpg")
    reasons = main_window._ai_run.apply_decision_reasons([soft])
    assert reasons == {soft.path: "Softer than its siblings"}


def test_the_hover_explanation_lists_the_flag_chips_for_a_cascade_result(main_window, tmp_path, monkeypatch):
    base = scene(1)
    save(tmp_path / "a1.jpg", base, 0)
    save(tmp_path / "a3_soft.jpg", base.filter(ImageFilter.GaussianBlur(5)), 2)
    run_analyze(main_window, monkeypatch, tmp_path, count=2)
    result = main_window._ai_run.ai_result_for_record(record(Path(tmp_path / "a3_soft.jpg")))
    why = build_ai_why(result)
    assert "Softer than its siblings" in why.flags and why.call == "Likely reject"
    assert pump_until(lambda: True)
