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
    main_window._ai_run.refresh_ai_state()
    yield main_window, paths
    main_window._ai_bundle = None
    main_window._ai_run.refresh_ai_state()
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
    """A folder database with the real schema holding one winner score per photo."""
    from aiculler.storage import SQLiteFeatureStore
    from image_triage.aiculler_workflow import build_aiculler_workflow_paths

    db_path = aiculler_db_path(build_aiculler_workflow_paths(str(tmp_path)))
    wow = {"a.jpg": 0.2, "b.jpg": 0.4, "c.jpg": 0.9, "d.jpg": 0.6}
    store = SQLiteFeatureStore(db_path)
    try:
        ensure_winner_scores_table(store.connection)
        for path in paths:
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            image_id = store.ensure_image(path)
            store.connection.execute(
                "INSERT INTO winner_scores (model_version, image_id, blended_score, per_folder_score, global_score, source, label_count)"
                " VALUES (?, ?, ?, NULL, NULL, 'global', 0)",
                (model_version, image_id, wow[name]),
            )
        store.connection.commit()
    finally:
        store.close()


@pytest.mark.parametrize("model_version", [WINNER_SCORE_FALLBACK_MODEL_VERSION, "adapter-v1"])
def test_ai_wow_sort_uses_stored_winner_scores(main_window, tmp_path, model_version) -> None:
    """Scores are stored under the adapter's model version once an adapter exists; the sort must still find them."""
    paths = make_jpegs(tmp_path, NAMES)
    _write_winner_scores(tmp_path, paths, model_version)  # as on a culled folder: the database exists before it is opened
    open_folder(main_window, tmp_path, len(NAMES))
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


def test_ai_dots_default_on_and_only_exist_after_a_cull(ai_folder) -> None:
    window, _ = ai_folder
    assert window._show_ai_tags_in_grid is True
    painted: list[object] = []
    original = window.grid._paint_ai_verdict_dot
    window.grid._paint_ai_verdict_dot = lambda painter, rect, result: (painted.append(result), original(painter, rect, result))
    try:
        window.grid.viewport().grab()
        assert painted and all(result is not None for result in painted), "cards did not paint an AI verdict dot after a cull"
        for record in window._records:
            assert window.grid._ai_verdict_dot_color(window._ai_run.ai_result_for_record(record)) is not None
    finally:
        window.grid._paint_ai_verdict_dot = original
    assert window.grid._ai_verdict_dot_color(None) is None


def test_ai_dot_setting_turns_the_dots_off(ai_folder) -> None:
    window, _ = ai_folder
    result = window._ai_run.ai_result_for_record(window._records[0])
    window.grid.set_show_ai_annotations(False)
    try:
        assert window.grid._ai_verdict_dot_color(result) is None
    finally:
        window.grid.set_show_ai_annotations(True)


def test_inspector_shows_wow_rank_and_disagreement_rows(ai_folder, tmp_path) -> None:
    window, paths = ai_folder
    _write_winner_scores(tmp_path, paths, "adapter-v1")
    window._scan.refresh_winner_scores_for_current_folder()
    name = "c.jpg"  # highest Wow score
    window.grid.set_current_index(_visible_names(window).index(name))
    window._inspector.update_inspector_context()
    rows = window.inspector_panel.ai_rows
    assert rows["Wow rank"].value_label.text() == "#1 of 4"
    assert rows["Disagreement"].value_label.text() == "None"


def test_bucket_bar_is_hidden_until_there_are_ai_results(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, NAMES)
    open_folder(main_window, tmp_path, len(NAMES))
    main_window._ai_bundle = None
    main_window._ai_run.refresh_ai_bucket_bar()
    assert main_window.ai_bucket_bar.isHidden()


def test_bucket_bar_counts_and_filters_by_verdict(ai_folder) -> None:
    from image_triage.ai_results import AICullBucket, ai_cull_bucket_for_result

    window, _ = ai_folder
    window._ai_run.refresh_ai_bucket_bar()
    bar = window.ai_bucket_bar
    assert not bar.isHidden()
    expected = {}
    for record in window._records:
        bucket = ai_cull_bucket_for_result(window._ai_run.ai_result_for_record(record))
        expected[bucket] = expected.get(bucket, 0) + 1
    for bucket, count in expected.items():
        assert bar.chip(bucket).text().endswith(f" {count}")
    target = next(iter(expected))
    try:
        bar.chip(target).click()
        assert window._filter_query.ai_cull_bucket == target
        assert len(window._records) == expected[target]
        bar.chip(target).click()
        assert window._filter_query.ai_cull_bucket is None
        assert len(window._records) == len(NAMES)
    finally:
        window._filter_query.ai_cull_bucket = None
        window._records_view.apply_filter_query_change()


def test_opening_a_culled_folder_shows_the_bucket_bar(main_window, tmp_path) -> None:
    paths = make_jpegs(tmp_path, NAMES)
    _write_ranked_export(tmp_path, paths)
    main_window._ai_bundle = None
    main_window._hidden_ai_results_checked_scope_key = None
    open_folder(main_window, tmp_path, len(NAMES))
    try:
        assert pump_until(lambda: not main_window.ai_bucket_bar.isHidden(), timeout=15.0)
    finally:
        main_window._ai_run.clear_ai_results_state()
        assert main_window.ai_bucket_bar.isHidden()
