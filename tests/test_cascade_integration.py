"""Analyze on the real MainWindow runs the stack-first cascade and its decisions show up where the person looks: the results bundle, the bucket bar,
the inspector. Offscreen; the rankers are not involved (no AI runtime in the test environment)."""

from __future__ import annotations

from pathlib import Path

import pytest

from image_triage.ai_results import AICullBucket, ai_cull_bucket_for_result, find_ai_result_for_record
from image_triage.cascade.launch import decide_ranking
from tests.harness import open_folder, pump_until
from tests.test_cascade_run import record, save, scene  # the generated-folder helpers

from PIL import Image, ImageFilter


@pytest.fixture
def cascade_folder(tmp_path):
    base = scene(1)
    save(tmp_path / "a1.jpg", base, 0)
    save(tmp_path / "a2.jpg", base.point(lambda v: min(255, v + 1)), 1)
    save(tmp_path / "a3_soft.jpg", base.filter(ImageFilter.GaussianBlur(5)), 2)
    save(tmp_path / "b1.jpg", scene(2), 40)
    save(tmp_path / "w1.jpg", Image.new("RGB", (480, 320), (255, 255, 255)), 120)
    return tmp_path


def run_analyze(main_window, monkeypatch, folder: Path, decision=None, count: int = 5) -> None:
    dialogs: list[str] = []
    monkeypatch.setattr(main_window, "_exec_dialog_with_geometry", lambda dialog, key: dialogs.append(key))
    monkeypatch.setattr(main_window._ai_run, "cascade_ranking_decision", lambda: decision or decide_ranking(None, False))
    open_folder(main_window, folder, count)
    main_window._ai_run.analyze_current_folder()
    assert main_window._active_ai_task is not None
    assert pump_until(lambda: main_window._active_ai_task is None and main_window._ai_bundle is not None, timeout=30.0)
    assert dialogs == ["ai_review_complete"]
    # Writing the results folder makes the window rescan; wait for the grid to settle before looking at it.
    assert pump_until(lambda: len(main_window._records) == count, timeout=15.0)


def test_analyze_runs_the_cascade_and_loads_its_results(main_window, cascade_folder, monkeypatch) -> None:
    run_analyze(main_window, monkeypatch, cascade_folder)
    bundle = main_window._ai_bundle
    by_name = {Path(path).name: result for path, result in bundle.results_by_path.items()}
    assert len(by_name) == 5
    assert by_name["a3_soft.jpg"].cascade_role == "rejected"
    assert by_name["w1.jpg"].cascade_role == "no_pick"
    assert {by_name["a1.jpg"].cascade_role, by_name["a2.jpg"].cascade_role} == {"final", "duplicate"}
    assert Path(bundle.export_csv_path).parent.name == "ranker_report" or Path(bundle.export_csv_path).exists()


def test_the_bucket_bar_and_inspector_describe_the_cleanup(main_window, cascade_folder, monkeypatch) -> None:
    run_analyze(main_window, monkeypatch, cascade_folder)
    assert not main_window.ai_bucket_bar.isHidden()
    counts = main_window._ai_run.ai_review_bucket_counts(main_window._ai_bundle)
    assert counts[AICullBucket.REJECT] == 1  # the blurred frame
    assert counts[AICullBucket.KEEPER] == 2  # the surviving duplicate-pair frame and the lone second scene
    assert counts[AICullBucket.NEEDS_REVIEW] == 2  # the collapsed duplicate and the blown-out frame
    assert counts[AICullBucket.AI_PICK] == 0  # nothing was ranked, so no winner is offered

    soft = record(cascade_folder / "a3_soft.jpg")
    result = find_ai_result_for_record(main_window._ai_bundle, soft)
    assert ai_cull_bucket_for_result(result) is AICullBucket.REJECT
    assert any("softer than its siblings" in line for line in result.explanation_lines)


def test_the_analyze_card_reports_the_cleanup_when_it_is_done(main_window, cascade_folder, monkeypatch) -> None:
    run_analyze(main_window, monkeypatch, cascade_folder)
    main_window._ai_run.refresh_inspector_analysis()
    card = main_window.inspector_panel.analyze_card
    assert card.summary_label.text() != ""


def test_flagged_frames_are_dimmed_in_the_grid_and_nothing_else_is(main_window, cascade_folder, monkeypatch) -> None:
    run_analyze(main_window, monkeypatch, cascade_folder)
    grid = main_window.grid
    by_name = {Path(path).name: result for path, result in main_window._ai_bundle.results_by_path.items()}
    assert grid._ai_flag_dim_alpha(by_name["a3_soft.jpg"]) > 0
    assert all(grid._ai_flag_dim_alpha(r) == 0 for name, r in by_name.items() if name != "a3_soft.jpg")
    assert grid._ai_flag_dim_alpha(None) == 0

    dimmed: list[str] = []
    original = grid._paint_ai_flag_dim
    grid._paint_ai_flag_dim = lambda painter, rect, result: (dimmed.append(getattr(result, "file_name", "")), original(painter, rect, result))
    try:
        grid.viewport().grab()
    finally:
        grid._paint_ai_flag_dim = original
    assert "a3_soft.jpg" in dimmed  # the card actually went through the dim step when painted


def test_turning_ai_marks_off_also_turns_the_dim_off(main_window, cascade_folder, monkeypatch) -> None:
    run_analyze(main_window, monkeypatch, cascade_folder)
    main_window.grid.set_show_ai_annotations(False)
    try:
        painted = []
        original = main_window.grid._paint_ai_flag_dim

        def spy(painter, rect, result):
            painted.append(main_window.grid._ai_flag_dim_alpha(result) and main_window.grid._show_ai_annotations)
            return original(painter, rect, result)

        main_window.grid._paint_ai_flag_dim = spy
        main_window.grid.viewport().grab()
        assert not any(painted)
    finally:
        main_window.grid._paint_ai_flag_dim = original
        main_window.grid.set_show_ai_annotations(True)
