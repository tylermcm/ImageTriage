"""WI-6.5 / audit I3: the hover "why" on a grid card.

The module is pure, so the wording and thresholds are pinned here; the grid tests check it shows only
when "Show AI tags on cards in the grid" is on, only for a photo that has an AI result, and that the
Winner / Reject button tooltips and the elided-filename tooltip are not lost.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from image_triage import ai_why
from image_triage.ai_results import AIConfidenceBucket, AIImageResult
from image_triage.ai_why import (
    CATEGORY_LABELS,
    FLAG_LABELS,
    AIWhy,
    ai_why_lines,
    ai_why_tooltip_html,
    build_ai_why,
    category_label,
    exposure_text,
    flag_labels,
    quality_text,
    sharpness_text,
)
from image_triage.grid import ThumbnailGridView
from image_triage.models import ImageRecord
from image_triage.review_intelligence import ReviewInsight
from image_triage.thumbnails import ThumbnailManager


def _result(**overrides) -> AIImageResult:
    values = dict(
        image_id="a",
        file_path="C:/shoot/a.jpg",
        file_name="a.jpg",
        group_id="g1",
        group_size=3,
        rank_in_group=1,
        score=0.8,
        confidence_bucket=AIConfidenceBucket.LIKELY_KEEPER,
        confidence_summary="Clear lead inside its AI group.",
    )
    values.update(overrides)
    return AIImageResult(**values)


# --------------------------------------------------------------------------- verdicts
@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (None, ""),
        (0, ""),
        (-3, ""),
        (1, "Blur detected (1)"),
        (39.9, "Blur detected (40)"),  # rounds for display, the verdict uses the real value
        (40, "Acceptable (40)"),
        (69.9, "Acceptable (70)"),
        (70, "Sharp (70)"),
        (100, "Sharp (100)"),
    ],
)
def test_sharpness_verdicts_and_their_cut_offs(score, expected) -> None:
    assert sharpness_text(score) == expected


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (None, ""),
        (0, ""),
        (20, "Poor (20)"),
        (39.9, "Poor (40)"),
        (40, "Uneven (40)"),
        (59.9, "Uneven (60)"),
        (60, "Balanced (60)"),
        (95, "Balanced (95)"),
    ],
)
def test_exposure_verdicts_and_their_cut_offs(score, expected) -> None:
    assert exposure_text(score) == expected


def test_quality_score_is_shown_to_two_places_and_hidden_when_missing() -> None:
    assert quality_text(0.6149) == "0.61"
    assert quality_text(0.0) == "0.00"  # a real, very low score is still shown
    assert quality_text(None) == ""


def test_sharpness_cut_offs_match_the_inspectors_focus_row() -> None:
    """The card and the Inspector must not disagree about what counts as sharp."""
    from image_triage.ui.docks import InspectorPanel

    inspector_word = {"Sharp": "Sharp", "Acceptable": "Acceptable", "Blur detected": "Blur detected"}
    for score in (1, 25, 39.9, 40, 55, 69.9, 70, 85, 100):
        card = sharpness_text(score).split(" (")[0]
        assert card == inspector_word[InspectorPanel._focus_level(score)], score


def test_category_names_match_the_inspectors_subject_row_for_every_category() -> None:
    from image_triage.ui.docks import InspectorPanel

    for category, label in CATEGORY_LABELS.items():
        assert InspectorPanel._subject_profile_text(category)[0] == label, category


def test_every_category_in_the_shipped_csv_has_a_label() -> None:
    import csv
    from pathlib import Path

    csv_path = Path(__file__).resolve().parents[1] / "aiculler" / "resources" / "categories.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        shipped = {row["category"].strip() for row in csv.DictReader(handle)} - {"category", ""}
    assert shipped and shipped <= set(CATEGORY_LABELS), shipped - set(CATEGORY_LABELS)


def test_every_technical_flag_the_pipeline_can_trigger_has_a_label() -> None:
    import csv
    from pathlib import Path

    csv_path = Path(__file__).resolve().parents[1] / "aiculler" / "resources" / "tag_penalties.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        shipped = {row["tag"].strip() for row in csv.DictReader(handle)}
    assert shipped and shipped <= set(FLAG_LABELS), shipped - set(FLAG_LABELS)


# --------------------------------------------------------------------------- flags and category
def test_flags_are_labelled_deduplicated_and_unknown_ones_made_readable() -> None:
    assert flag_labels("blownout,motionblur") == ("Blown highlights", "Motion blur")
    assert flag_labels(" blownout , ,blownout ") == ("Blown highlights",)
    assert flag_labels("BlownOut") == ("Blown highlights",)
    assert flag_labels("some_new_flag") == ("Some new flag",)
    assert flag_labels("") == () and flag_labels(None) == ()


def test_category_is_hidden_when_unknown_or_uncategorized() -> None:
    assert category_label("people_portrait") == "Portrait"
    assert category_label("uncategorized") == ""
    assert category_label("  ") == "" and category_label(None) == ""
    assert category_label("brand_new_kind") == "Brand New Kind"


# --------------------------------------------------------------------------- assembling it
def test_nothing_to_explain_without_an_ai_result() -> None:
    assert build_ai_why(None) is None
    assert build_ai_why(None, ReviewInsight(path="x", detail_score=90.0)) is None


def test_a_bare_result_gives_only_the_call_and_the_reason() -> None:
    why = build_ai_why(_result())
    assert why == AIWhy(call="Likely winner", reasons=("Clear lead inside its AI group",))
    assert ai_why_lines(why) == ("Likely winner", "Clear lead inside its AI group")


def test_every_available_signal_appears() -> None:
    result = _result(
        technical_score=0.6149,
        triggered_tags="blownout,motionblur",
        primary_category="people_portrait",
        cluster_reason="Part of a burst of 3 near-identical frames.",
        confidence_summary="Clear lead inside its AI group. Demoted because a stronger frame already passes.",
    )
    insight = ReviewInsight(path="C:/shoot/a.jpg", detail_score=82.0, exposure_score=91.0)
    assert ai_why_lines(build_ai_why(result, insight)) == (
        "Likely winner",
        "Clear lead inside its AI group",
        "Demoted because a stronger frame already passes",
        "Part of a burst of 3 near-identical frames",
        "Sharpness: Sharp (82)  \u00b7  Exposure: Balanced (91)",
        "Quality score: 0.61",
        "Flags: Blown highlights, Motion blur",
        "Subject: Portrait",
    )


def test_sharpness_without_exposure_and_the_other_way_round() -> None:
    assert "Sharpness: Soft" not in "\n".join(ai_why_lines(build_ai_why(_result())))
    only_sharp = ai_why_lines(build_ai_why(_result(), ReviewInsight(path="x", detail_score=50.0)))
    assert "Sharpness: Acceptable (50)" in only_sharp and not any("Exposure" in line for line in only_sharp)
    only_exposure = ai_why_lines(build_ai_why(_result(), ReviewInsight(path="x", exposure_score=30.0)))
    assert "Exposure: Poor (30)" in only_exposure and not any("Sharpness" in line for line in only_exposure)


def test_reasons_are_split_deduplicated_capped_and_trimmed() -> None:
    result = _result(
        confidence_summary="One. Two. two. Three. Four.",
        cluster_reason="Five.",
    )
    assert build_ai_why(result).reasons == ("One", "Two", "Three")
    long_sentence = "x" * 400
    reason = build_ai_why(_result(confidence_summary=long_sentence, cluster_reason="")).reasons[0]
    assert len(reason) == ai_why.MAX_REASON_CHARS and reason.endswith("\u2026")
    assert build_ai_why(_result(confidence_summary="", cluster_reason="")).reasons == ()


def test_the_call_follows_the_bucket() -> None:
    expected = {
        AIConfidenceBucket.OBVIOUS_WINNER: "Obvious winner",
        AIConfidenceBucket.LIKELY_KEEPER: "Likely winner",
        AIConfidenceBucket.NEEDS_REVIEW: "Needs review",
        AIConfidenceBucket.LIKELY_REJECT: "Likely reject",
    }
    for bucket, call in expected.items():
        assert build_ai_why(_result(confidence_bucket=bucket)).call == call


def test_tooltip_html_is_escaped_and_optionally_ends_with_the_filename() -> None:
    why = build_ai_why(_result(confidence_summary="Looks <b>odd</b> & soft."))
    html_text = ai_why_tooltip_html(why)
    assert html_text.startswith("<b>Likely winner</b><br>")
    assert "&lt;b&gt;odd&lt;/b&gt; &amp; soft" in html_text and "<b>odd</b>" not in html_text
    with_name = ai_why_tooltip_html(why, filename="IMG_0001 <final>.jpg")
    assert with_name.endswith("IMG_0001 &lt;final&gt;.jpg</span>")
    assert ai_why_tooltip_html(AIWhy(call="Needs review")) == "<b>Needs review</b>"


# --------------------------------------------------------------------------- the grid
def _grid(*, show_ai: bool, with_result: bool = True, insight: bool = True):
    app = QApplication.instance() or QApplication([])
    grid = ThumbnailGridView(ThumbnailManager())
    grid.resize(900, 600)
    grid.show()  # an unshown scroll area never lays out its viewport, so no card has a real rectangle
    app.processEvents()
    records = [
        ImageRecord(path="C:/shoot/a.jpg", name="a.jpg", size=10, modified_ns=1),
        ImageRecord(path="C:/shoot/b.jpg", name="b.jpg", size=10, modified_ns=1),
    ]
    grid.set_items(records, emit_state_signals=False, request_thumbnails=False)
    grid.set_show_ai_annotations(show_ai)
    if with_result:
        grid.set_ai_results({"c:/shoot/a.jpg": _result(technical_score=0.6, primary_category="landscape")})
    if insight:
        grid.set_review_insights({"C:/shoot/a.jpg": ReviewInsight(path="C:/shoot/a.jpg", detail_score=80.0, exposure_score=70.0)})
    app.processEvents()
    return grid


def _card_center(grid, index: int) -> QPoint:
    return grid._item_rect(index).center()


def _hover(grid, point: QPoint) -> str:
    """Deliver a real mouse-move event at ``point`` and return the viewport's tooltip.

    Sent directly rather than with ``QTest.mouseMove``: on the offscreen platform that warps the cursor
    and relies on the platform delivering the move, which is timing-dependent."""
    viewport = grid.viewport()
    local = QPointF(point)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        local,
        local,
        QPointF(viewport.mapToGlobal(point)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewport, event)
    return viewport.toolTip()


def test_hovering_a_card_explains_the_ai_call_when_ai_tags_are_on() -> None:
    grid = _grid(show_ai=True)
    tip = _hover(grid, _card_center(grid, 0))
    assert tip.startswith("<b>Likely winner</b>")
    for expected in ("Clear lead inside its AI group", "Sharpness: Sharp (80)", "Exposure: Balanced (70)", "Quality score: 0.60", "Subject: Landscape"):
        assert expected in tip, expected
    grid.deleteLater()


def test_hovering_is_unchanged_when_ai_tags_are_off() -> None:
    grid = _grid(show_ai=False)
    assert _hover(grid, _card_center(grid, 0)) == ""  # a short filename is not elided, so no tooltip at all, as before
    grid.deleteLater()


def test_a_card_with_no_ai_result_gets_no_why() -> None:
    grid = _grid(show_ai=True)
    assert "Likely winner" not in _hover(grid, _card_center(grid, 1))  # b.jpg has no result
    assert grid._ai_why_for(1) is None
    grid.deleteLater()


def test_the_why_comes_without_review_insight_too() -> None:
    grid = _grid(show_ai=True, insight=False)
    tip = grid._card_tooltip(0, grid._item_rect(0))
    assert "Likely winner" in tip and "Sharpness" not in tip and "Exposure" not in tip
    grid.deleteLater()


def test_a_cut_off_filename_still_appears_under_the_why() -> None:
    grid = _grid(show_ai=True)
    with_name = ai_why_tooltip_html(build_ai_why(_result()), filename="a-very-long-name.jpg")
    grid._filename_tooltip = lambda index, rect: "a-very-long-name.jpg"
    assert grid._card_tooltip(0, grid._item_rect(0)).endswith("a-very-long-name.jpg</span>")
    assert with_name.endswith("a-very-long-name.jpg</span>")
    grid.deleteLater()


def test_the_winner_and_reject_buttons_keep_their_own_tooltips() -> None:
    grid = _grid(show_ai=True)
    rect = grid._item_rect(0)
    assert "Mark Winner" in _hover(grid, grid._winner_button_hit_rect(rect).center())
    assert "Reject" in _hover(grid, grid._reject_button_hit_rect(rect).center())
    assert "Likely winner" not in grid.viewport().toolTip()  # the button wins over the card-level why
    grid.deleteLater()


def test_toggling_ai_tags_switches_the_why_on_and_off_live() -> None:
    grid = _grid(show_ai=True)
    assert grid._ai_why_for(0) is not None
    grid.set_show_ai_annotations(False)
    assert grid._ai_why_for(0) is None
    grid.set_show_ai_annotations(True)
    assert grid._ai_why_for(0) is not None
    grid.deleteLater()
