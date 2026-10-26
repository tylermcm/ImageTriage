"""The inspector's Analyze folder control: AI styling, whole-folder runs, hiding under a filter, progress, reduced motion."""
from __future__ import annotations

import os
import pytest
from PySide6.QtWidgets import QApplication

from image_triage.filtering import RecordFilterQuery
from image_triage.models import FilterMode
from image_triage.ui.ai_analyze_card import AIAnalyzeCard, animations_enabled
from tests.harness import make_jpegs, open_folder


@pytest.fixture
def card():
    app = QApplication.instance() or QApplication([])
    widget = AIAnalyzeCard()
    widget.show()
    app.processEvents()
    yield widget
    widget.close()


def test_card_switches_between_button_progress_and_hint(card) -> None:
    assert card.state() == "idle" and not card.summary_label.isVisible()
    card.set_running(stage="Scoring photos", current=1240, total=3420, eta="4m", stopping=False)
    assert card.state() == "running"
    assert "1,240 of 3,420" in card.progress.detail_label.text()
    assert "4m" in card.progress.detail_label.text()
    assert card.progress.bar.fraction() == pytest.approx(1240 / 3420)
    card.set_blocked("Clear the filter.", can_clear_filter=True)
    assert card.state() == "blocked" and not card.clear_filter_button.isHidden()
    card.set_idle(has_results=True, summary="3,420 analyzed")
    assert card.state() == "idle" and card.summary_label.text() == "3,420 analyzed"


def test_button_click_cancel_and_clear_filter_emit_signals(card) -> None:
    seen: list[str] = []
    card.analyze_requested.connect(lambda: seen.append("analyze"))
    card.cancel_requested.connect(lambda: seen.append("cancel"))
    card.clear_filter_requested.connect(lambda: seen.append("clear"))
    card.button.click()
    card.set_running(stage="x", current=0, total=0, eta="")
    card.progress.cancel_button.click()
    card.set_blocked("hint", can_clear_filter=True)
    card.clear_filter_button.click()
    assert seen == ["analyze", "cancel", "clear"]


def test_stopping_disables_cancel_and_says_so(card) -> None:
    card.set_running(stage="Scoring", current=5, total=10, eta="1m", stopping=True)
    assert card.progress.stage_label.text() == "Stopping..."
    assert not card.progress.cancel_button.isEnabled()


def test_only_the_visible_page_sizes_the_card(card) -> None:
    card.set_idle(has_results=False)
    idle_height = card.sizeHint().height()
    card.set_running(stage="x", current=1, total=2, eta="")
    assert card.sizeHint().height() > idle_height
    card.set_idle(has_results=False)
    assert card.sizeHint().height() == idle_height


def test_animation_runs_and_stops_with_the_motion_setting(monkeypatch, card) -> None:
    # Exercise the toggle independently of the desktop's current motion setting.
    monkeypatch.setattr("image_triage.ui.ai_analyze_card.animations_enabled", lambda: os.environ.get("IMAGE_TRIAGE_REDUCE_MOTION") != "1")
    monkeypatch.delenv("IMAGE_TRIAGE_REDUCE_MOTION", raising=False)
    card.refresh_motion_setting()
    assert card.button.is_animating()
    monkeypatch.setenv("IMAGE_TRIAGE_REDUCE_MOTION", "1")
    assert animations_enabled() is False
    card.refresh_motion_setting()
    assert not card.button.is_animating()  # motion stops; the painted gradient border and sparkle remain


def test_animation_pauses_when_hidden(monkeypatch, card) -> None:
    monkeypatch.setattr("image_triage.ui.ai_analyze_card.animations_enabled", lambda: os.environ.get("IMAGE_TRIAGE_REDUCE_MOTION") != "1")
    monkeypatch.delenv("IMAGE_TRIAGE_REDUCE_MOTION", raising=False)
    card.refresh_motion_setting()
    assert card.button.is_animating()
    card.hide()
    QApplication.processEvents()
    assert not card.button.is_animating()


# ---- on the real window -------------------------------------------------------------------------------------------
def _panel_state(window) -> str:
    return window.inspector_panel.analyze_card.state()


def test_filter_hides_the_button_and_clearing_it_brings_it_back(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    open_folder(main_window, tmp_path, 3)
    main_window._ai_run.refresh_inspector_analysis()
    assert _panel_state(main_window) == "idle"
    try:
        main_window._filter_query.quick_filter = FilterMode.UNREVIEWED
        main_window._records_view.apply_filter_query_change()
        assert _panel_state(main_window) == "blocked"
        assert "whole folder" in main_window.inspector_panel.analyze_card.blocked_label.text()
        main_window.inspector_panel.analyze_card.clear_filter_button.click()
        assert not main_window._filter_query.has_active_filters
        assert _panel_state(main_window) == "idle"
    finally:
        main_window._filter_query = RecordFilterQuery()
        main_window._records_view.apply_filter_query_change()


def _pretend_ai_is_installed(window, monkeypatch) -> None:
    monkeypatch.setattr(window._settings_ctl, "configured_aiculler_runtime", lambda **_k: type("R", (), {"validate": lambda self: None})())
    monkeypatch.setattr(window._ai_setup, "refresh_ai_runtime_preferences", lambda: None)


def test_clicking_analyze_runs_the_whole_folder_every_time(main_window, tmp_path, monkeypatch) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    open_folder(main_window, tmp_path, 3)
    runs: list[int] = []
    monkeypatch.setattr(main_window._ai_run, "run_ai_pipeline", lambda: runs.append(len([r for r in main_window._all_records if not r.is_folder])))
    _pretend_ai_is_installed(main_window, monkeypatch)
    main_window._ai_bundle = None
    main_window.inspector_panel.analyze_button.click()
    main_window.inspector_panel.analyze_button.click()  # a second click runs again: no "already analyzed" confirmation
    assert runs == [3, 3]


def test_analyze_does_not_run_while_a_filter_narrows_the_view(main_window, tmp_path, monkeypatch) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    open_folder(main_window, tmp_path, 2)
    ran: list[int] = []
    monkeypatch.setattr(main_window._ai_run, "run_ai_pipeline", lambda: ran.append(1))
    _pretend_ai_is_installed(main_window, monkeypatch)
    try:
        main_window._filter_query.quick_filter = FilterMode.UNREVIEWED
        main_window._ai_run.analyze_current_folder()
        assert ran == []
    finally:
        main_window._filter_query = RecordFilterQuery()
        main_window._records_view.apply_filter_query_change()


def _broken_runtime(main_window, monkeypatch, opened: list[str], ran: list[int]) -> None:
    class Broken:
        def validate(self):
            raise RuntimeError("models missing")

    monkeypatch.setattr(main_window._settings_ctl, "configured_aiculler_runtime", lambda **_k: Broken())
    monkeypatch.setattr(main_window._ai_setup, "refresh_ai_runtime_preferences", lambda: None)
    monkeypatch.setattr(main_window._handoff, "open_ai_workflow_center", lambda: opened.append("setup"))
    monkeypatch.setattr(main_window._ai_run, "run_ai_pipeline", lambda: ran.append(1))


def test_the_default_cleanup_runs_without_the_ai_runtime(main_window, tmp_path, monkeypatch) -> None:
    # The cascade's cheap passes need no AI runtime, so Analyze must not send the person to setup first.
    make_jpegs(tmp_path, ["a.jpg"])
    open_folder(main_window, tmp_path, 1)
    opened: list[str] = []
    ran: list[int] = []
    _broken_runtime(main_window, monkeypatch, opened, ran)
    main_window._ai_run.analyze_current_folder()
    assert opened == [] and ran == [1]


def test_unconfigured_ai_opens_setup_for_the_legacy_culler(main_window, tmp_path, monkeypatch) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    open_folder(main_window, tmp_path, 1)
    opened: list[str] = []
    ran: list[int] = []
    _broken_runtime(main_window, monkeypatch, opened, ran)
    monkeypatch.setattr(main_window._ai_run, "use_legacy_culler", lambda: True)
    main_window._ai_run.analyze_current_folder()
    assert opened == ["setup"] and ran == []


def test_running_state_shows_the_pipeline_progress_in_the_inspector(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    open_folder(main_window, tmp_path, 1)

    class FakeTask:
        folder = str(tmp_path)

    main_window._active_ai_task = FakeTask()
    main_window._ai_stage_message = "Scoring photos"
    main_window._ai_progress_current, main_window._ai_progress_total = 40, 100
    main_window._ai_progress_eta_text = "2m"
    try:
        main_window._ai_run.refresh_inspector_analysis()
        card = main_window.inspector_panel.analyze_card
        assert card.state() == "running"
        assert "40 of 100" in card.progress.detail_label.text()
        assert card.progress.stage_label.text() == "Scoring photos"
    finally:
        main_window._active_ai_task = None
        main_window._ai_stage_message = ""
        main_window._ai_run.refresh_inspector_analysis()
    assert _panel_state(main_window) == "idle"


def test_cancel_in_the_inspector_stops_the_run(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    open_folder(main_window, tmp_path, 1)
    cancelled: list[int] = []

    class FakeTask:
        folder = str(tmp_path)

        def cancel(self):
            cancelled.append(1)

    main_window._active_ai_task = FakeTask()
    try:
        main_window._ai_run.refresh_inspector_analysis()
        main_window.inspector_panel.analyze_card.progress.cancel_button.click()
        assert cancelled == [1]
    finally:
        main_window._active_ai_task = None
        main_window._ai_stage_message = ""
        main_window._ai_run.refresh_inspector_analysis()


def test_results_summary_is_shown_next_to_the_button(main_window, tmp_path) -> None:
    from tests.test_ai_culling_end_to_end import NAMES, _bundle

    paths = make_jpegs(tmp_path, NAMES)
    open_folder(main_window, tmp_path, len(NAMES))
    main_window._ai_bundle = _bundle(paths, tmp_path)
    try:
        main_window._ai_run.refresh_inspector_analysis()
        card = main_window.inspector_panel.analyze_card
        assert card.state() == "idle"
        assert card.summary_label.text().startswith("4 analyzed")
    finally:
        main_window._ai_bundle = None
        main_window._ai_run.refresh_inspector_analysis()
