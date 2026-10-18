"""End-to-end probes of the AI filters that read a folder's own files (Ingested, Prefilter dumped, Disagreements) and of the AI menu.

Each starts from data on disk, the way an AI run leaves it, and checks what the grid shows.
"""
from __future__ import annotations

import dataclasses
import json
import os

import pytest

from aiculler.storage import SQLiteFeatureStore
from image_triage.aiculler_workflow import aiculler_db_path, build_aiculler_workflow_paths
from image_triage.filtering import RecordFilterQuery
from image_triage.models import FilterMode
from image_triage.phash_prefilter import build_phash_prefilter_paths
from tests.harness import make_jpegs, open_folder

NAMES = ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]


def _reset_filters(window) -> None:
    window._filter_query = RecordFilterQuery()
    window._records_view.apply_filter_query_change()


def _visible(window) -> list[str]:
    return sorted(record.name for record in window._records)


def _choose(window, mode: FilterMode) -> None:
    window.filter_combo.setCurrentIndex(window.filter_combo.findData(mode))


def _write_ingested_db(folder, paths: list[str], ingested: list[str]) -> None:
    """A folder database with the real schema: only the named photos have an embedding and status 'ready'."""
    db_path = aiculler_db_path(build_aiculler_workflow_paths(str(folder)))
    store = SQLiteFeatureStore(db_path)
    try:
        for path in paths:
            name = os.path.basename(path)
            image_id = store.upsert_image(path, status="ready" if name in ingested else "pending")
            if name in ingested:
                store.save_semantic_embedding(image_id, [0.1, 0.2, 0.3, 0.4])
    finally:
        store.close()


def _write_prefilter_rows(folder, rows: list[dict]) -> None:
    paths = build_phash_prefilter_paths(str(folder))
    paths.ensure()
    paths.rows_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_ingested_filter_shows_only_photos_the_ai_has_embedded(main_window, tmp_path) -> None:
    paths = make_jpegs(tmp_path, NAMES)
    _write_ingested_db(tmp_path, paths, ingested=["a.jpg", "c.jpg"])
    open_folder(main_window, tmp_path, len(NAMES))
    try:
        _choose(main_window, FilterMode.AI_INGESTED)
        assert _visible(main_window) == ["a.jpg", "c.jpg"]
    finally:
        _reset_filters(main_window)
    assert _visible(main_window) == NAMES


def test_ingested_filter_is_empty_for_a_folder_never_analyzed(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, NAMES)
    open_folder(main_window, tmp_path, len(NAMES))
    try:
        _choose(main_window, FilterMode.AI_INGESTED)
        assert _visible(main_window) == []
    finally:
        _reset_filters(main_window)


def test_prefilter_dumped_filter_shows_quarantined_and_removed_photos_only(main_window, tmp_path) -> None:
    paths = make_jpegs(tmp_path, NAMES)
    _write_prefilter_rows(
        tmp_path,
        [
            {"path": paths[0], "action": "quarantine", "reason": "phash_duplicate_trash", "score": 0.9},
            {"path": paths[1], "action": "remove_from_pool", "reason": "phash_duplicate_trash", "score": 0.8},
            {"path": paths[2], "action": "rescued", "reason": "phash_duplicate_trash", "score": 0.5},
            {"path": paths[3], "action": "pass", "reason": "", "score": 0.0},
        ],
    )
    open_folder(main_window, tmp_path, len(NAMES))
    try:
        _choose(main_window, FilterMode.AI_PREFILTER_DUMPED)
        assert _visible(main_window) == ["a.jpg", "b.jpg"]
    finally:
        _reset_filters(main_window)


def test_prefilter_decisions_follow_the_open_folder(main_window, tmp_path_factory) -> None:
    first, second = tmp_path_factory.mktemp("first"), tmp_path_factory.mktemp("second")
    first_paths = make_jpegs(first, NAMES)
    make_jpegs(second, NAMES)
    _write_prefilter_rows(first, [{"path": first_paths[0], "action": "quarantine", "score": 0.9}])
    open_folder(main_window, first, len(NAMES))
    try:
        _choose(main_window, FilterMode.AI_PREFILTER_DUMPED)
        assert _visible(main_window) == ["a.jpg"]
        _reset_filters(main_window)
        open_folder(main_window, second, len(NAMES))
        _choose(main_window, FilterMode.AI_PREFILTER_DUMPED)
        assert _visible(main_window) == []
    finally:
        _reset_filters(main_window)


def test_disagreements_filter_needs_ai_results_and_labels_the_menu_state(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, NAMES)
    open_folder(main_window, tmp_path, len(NAMES))
    main_window._ai_bundle = None
    main_window._ai_run.update_ai_toolbar_state()
    assert not main_window.actions.review_ai_disagreements.isEnabled()
    assert not main_window.actions.filter_actions[FilterMode.AI_DISAGREEMENTS].isEnabled()


# ---- the AI menu ----------------------------------------------------------------------------------------------------
def _ai_menu_actions(window) -> dict[str, object]:
    menu = next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "AI")
    found = {}

    def walk(m):
        for action in m.actions():
            if action.menu() is not None:
                walk(action.menu())
            elif action.text():
                found[action.text().replace("&", "")] = action

    walk(menu)
    return found


def test_every_ai_menu_entry_has_a_working_handler(main_window) -> None:
    entries = _ai_menu_actions(main_window)
    assert entries, "the AI menu is empty"
    expected = {
        "Guided AI Cull...", "AI Workflow Center...", "Apply AI Decisions", "Next AI Top Pick", "Check AI Readiness...",
        "Set Up AI Runtime...", "Repair AI...", "Open AI Report", "Show AI Review Summary",
    }
    missing = expected - set(entries)
    assert not missing, f"AI menu lost entries: {sorted(missing)}"
    for name, action in entries.items():
        assert action.receivers("2triggered(bool)") > 0 or action.isCheckable(), f"AI menu entry {name!r} is connected to nothing"


def test_every_ai_action_is_reachable_from_the_menu_palette_or_toolbar(main_window) -> None:
    """The AI menu is deliberately short, so some actions live only in the command palette or the toolbar editor.
    An AI action that loses every route (as the retired AI mode once did) can never be run."""
    actions = main_window.actions
    ai_action_names = [
        name for name in (f.name for f in dataclasses.fields(actions))
        if ("ai" in name.split("_") or name.startswith("ai_")) and hasattr(getattr(actions, name), "triggered")
    ]
    assert len(ai_action_names) >= 15
    menu_actions = set(_ai_menu_actions(main_window).values())
    # the palette lists only enabled actions, so judge routing with every AI action enabled, then put the states back
    saved_states = {name: getattr(actions, name).isEnabled() for name in ai_action_names}
    for name in ai_action_names:
        getattr(actions, name).setEnabled(True)
    try:
        palette_targets = [getattr(c.callback, "__self__", None) for c in main_window._command_palette.build_commands("main")]
    finally:
        for name, enabled in saved_states.items():
            getattr(actions, name).setEnabled(enabled)
    toolbar_actions = {spec[0] for spec in main_window._toolbar.workspace_toolbar_action_specs().values()}
    unreachable = []
    for name in ai_action_names:
        action = getattr(actions, name)
        if not (action in menu_actions or any(target is action for target in palette_targets) or action in toolbar_actions):
            unreachable.append(name)
    assert not unreachable, f"AI actions with no route to the user: {unreachable}"


def test_results_menu_entries_follow_whether_results_are_loaded(main_window, tmp_path) -> None:
    from tests.test_ai_culling_end_to_end import NAMES as E2E_NAMES, _bundle

    paths = make_jpegs(tmp_path, E2E_NAMES)
    open_folder(main_window, tmp_path, len(E2E_NAMES))
    entries = _ai_menu_actions(main_window)
    clear = main_window.actions.clear_ai_results
    main_window._ai_bundle = None
    main_window._ai_run.update_ai_toolbar_state()
    assert not clear.isEnabled()
    assert not entries["Next AI Top Pick"].isEnabled()
    main_window._ai_bundle = _bundle(paths, tmp_path)
    try:
        main_window._ai_run.update_ai_toolbar_state()
        assert clear.isEnabled()
        assert entries["Next AI Top Pick"].isEnabled()
    finally:
        main_window._ai_bundle = None
        main_window._ai_run.update_ai_toolbar_state()


def test_clear_ai_results_removes_the_bar_and_the_inspector_summary(main_window, tmp_path) -> None:
    from tests.test_ai_culling_end_to_end import NAMES as E2E_NAMES, _bundle

    paths = make_jpegs(tmp_path, E2E_NAMES)
    open_folder(main_window, tmp_path, len(E2E_NAMES))
    main_window._ai_bundle = _bundle(paths, tmp_path)
    main_window._ai_run.refresh_ai_state()
    assert not main_window.ai_bucket_bar.isHidden()
    main_window._ai_run.clear_ai_results_state()
    assert main_window.ai_bucket_bar.isHidden()
    assert main_window.inspector_panel.analyze_card.summary_label.text() == ""
