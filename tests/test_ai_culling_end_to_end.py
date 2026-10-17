"""End-to-end probes of AI culling on the real MainWindow.

Each test starts from AI data (a result bundle or a folder database) and checks what the
user can see: sort order, filters, the inspector's AI analysis rows and winner-score sorting.
The pieces are unit-tested elsewhere; these catch the controllers being disconnected from them.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from image_triage.ai_results import AIConfidenceBucket, AIImageResult, build_ai_bundle_from_results
from image_triage.aiculler_workflow import (
    WINNER_SCORE_FALLBACK_MODEL_VERSION,
    aiculler_db_path,
    ensure_winner_scores_table,
)
from image_triage.models import FilterMode, SortMode
from tests.harness import make_jpegs, open_folder, pump_until

NAMES = ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
# name -> (score, bucket)
SCORES = {
    "a.jpg": (0.30, AIConfidenceBucket.LIKELY_REJECT),
    "b.jpg": (0.95, AIConfidenceBucket.OBVIOUS_WINNER),
    "c.jpg": (0.60, AIConfidenceBucket.NEEDS_REVIEW),
    "d.jpg": (0.80, AIConfidenceBucket.LIKELY_KEEPER),
}


def _bundle(paths: list[str], tmp_path):
    results = []
    for index, path in enumerate(paths):
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        score, bucket = SCORES[name]
        results.append(
            AIImageResult(
                image_id=str(index),
                file_path=path,
                file_name=name,
                group_id=f"g{index}",
                group_size=1,
                rank_in_group=1,
                score=score,
                confidence_bucket=bucket,
            )
        )
    return build_ai_bundle_from_results(
        source_path=tmp_path / "ranked.csv", export_csv_path=tmp_path / "ranked.csv", results=results
    )


@pytest.fixture
def ai_folder(main_window, tmp_path):
    paths = make_jpegs(tmp_path, NAMES)
    open_folder(main_window, tmp_path, len(NAMES))
    main_window._ai_bundle = _bundle(paths, tmp_path)
    yield main_window, paths
    main_window._ai_bundle = None
    main_window._winner_scores_by_path = {}
    main_window._sort_mode = SortMode.NAME


def _visible_names(window) -> list[str]:
    return [record.name for record in window._records]


def _apply_sort(window, mode: SortMode) -> None:
    window._views.set_sort_mode(mode)
    pump_until(lambda: window._sort_mode == mode)


def test_ai_rank_sort_orders_by_score(ai_folder) -> None:
    window, _ = ai_folder
    _apply_sort(window, SortMode.AI_RANK)
    assert _visible_names(window) == ["b.jpg", "d.jpg", "c.jpg", "a.jpg"]


def test_ai_top_picks_filter_keeps_only_keepers(ai_folder) -> None:
    window, _ = ai_folder
    expected = sorted(
        record.name for record in window._records if window._ai_run.ai_result_for_record(record).is_top_pick
    )
    assert expected and len(expected) < len(NAMES)
    try:
        window.filter_combo.setCurrentIndex(window.filter_combo.findData(FilterMode.AI_TOP_PICKS))
        assert sorted(_visible_names(window)) == expected
    finally:
        window.filter_combo.setCurrentIndex(window.filter_combo.findData(FilterMode.ALL))


def test_inspector_shows_ai_rows_for_the_current_photo(ai_folder) -> None:
    window, _ = ai_folder
    window.grid.set_current_index(_visible_names(window).index("b.jpg"))
    window._inspector.update_inspector_context()
    panel = window.inspector_panel
    assert panel.ai_stack.currentIndex() == 1, "AI analysis section still invites analysis despite loaded results"
    assert panel.ai_rows["Suggestion"].value_label.text() not in {"", "Unavailable"}


def test_inspector_invites_analysis_without_results(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, NAMES)
    open_folder(main_window, tmp_path, len(NAMES))
    main_window._ai_bundle = None
    main_window.grid.set_current_index(0)
    main_window._inspector.update_inspector_context()
    assert main_window.inspector_panel.ai_stack.currentIndex() == 0


def _write_winner_scores(tmp_path, paths: list[str], model_version: str) -> None:
    window_paths = None
    from image_triage.aiculler_workflow import build_aiculler_workflow_paths

    window_paths = build_aiculler_workflow_paths(str(tmp_path))
    db_path = aiculler_db_path(window_paths)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    wow = {"a.jpg": 0.2, "b.jpg": 0.4, "c.jpg": 0.9, "d.jpg": 0.6}
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY, source_path TEXT)")
        ensure_winner_scores_table(connection)
        for index, path in enumerate(paths, start=1):
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            connection.execute("INSERT OR REPLACE INTO images (id, source_path) VALUES (?, ?)", (index, path))
            connection.execute(
                "INSERT INTO winner_scores (model_version, image_id, blended_score, per_folder_score, global_score, source, label_count)"
                " VALUES (?, ?, ?, NULL, NULL, 'global', 0)",
                (model_version, index, wow[name]),
            )
        connection.commit()


@pytest.mark.parametrize("model_version", [WINNER_SCORE_FALLBACK_MODEL_VERSION, "adapter-v1"])
def test_ai_wow_sort_uses_stored_winner_scores(main_window, tmp_path, model_version) -> None:
    """Scores are stored under the adapter's model version once an adapter exists; the sort must still find them."""
    paths = make_jpegs(tmp_path, NAMES)
    open_folder(main_window, tmp_path, len(NAMES))
    _write_winner_scores(tmp_path, paths, model_version)
    main_window._winner_scores_by_path = {}
    try:
        _apply_sort(main_window, SortMode.AI_WOW)
        assert _visible_names(main_window) == ["c.jpg", "d.jpg", "b.jpg", "a.jpg"]
    finally:
        main_window._winner_scores_by_path = {}
        main_window._sort_mode = SortMode.NAME


def _write_ranked_export(folder, paths: list[str]) -> str:
    import csv

    from image_triage.ai_workflow import build_ai_workflow_paths

    ai_paths = build_ai_workflow_paths(str(folder))
    ai_paths.report_dir.mkdir(parents=True, exist_ok=True)
    with open(ai_paths.ranked_export_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["file_path", "file_name", "cluster_id", "cluster_size", "rank_in_cluster", "score"]
        )
        writer.writeheader()
        for index, path in enumerate(paths):
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            writer.writerow(
                {
                    "file_path": path,
                    "file_name": name,
                    "cluster_id": f"g{index}",
                    "cluster_size": 1,
                    "rank_in_cluster": 1,
                    "score": SCORES[name][0],
                }
            )
    return str(ai_paths.ranked_export_path)


def test_opening_a_folder_with_saved_ai_results_loads_them(main_window, tmp_path) -> None:
    paths = make_jpegs(tmp_path, NAMES)
    _write_ranked_export(tmp_path, paths)
    main_window._ai_bundle = None
    main_window._hidden_ai_results_checked_scope_key = None
    open_folder(main_window, tmp_path, len(NAMES))
    try:
        assert pump_until(lambda: main_window._ai_bundle is not None, timeout=15.0), (
            "a folder with a saved ranked export did not load its AI results"
        )
        assert len(main_window._ai_bundle.results_by_path) == len(NAMES)
    finally:
        main_window._ai_bundle = None


def test_restore_ai_results_reloads_the_saved_results_file(main_window, tmp_path) -> None:
    """Startup restore re-opens the results file the user last loaded for the open folder."""
    paths = make_jpegs(tmp_path, NAMES)
    export = _write_ranked_export(tmp_path, paths)
    open_folder(main_window, tmp_path, len(NAMES))
    main_window._ai_bundle = None
    main_window._settings.setValue(main_window.AI_RESULTS_KEY, export)
    try:
        assert main_window._ai_run.restore_ai_results() is True
        assert main_window._ai_bundle is not None
    finally:
        main_window._ai_bundle = None
        main_window._settings.remove(main_window.AI_RESULTS_KEY)


def test_ai_badges_in_the_grid_follow_the_setting(main_window) -> None:
    previous = main_window._show_ai_tags_in_grid
    try:
        main_window._show_ai_tags_in_grid = True
        main_window.grid.set_show_ai_annotations(True)
        assert main_window.grid._show_ai_annotations is True
    finally:
        main_window._show_ai_tags_in_grid = previous
        main_window.grid.set_show_ai_annotations(previous)
