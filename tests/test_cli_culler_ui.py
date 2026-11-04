import os

import pytest

from image_triage.cli_culler_results import native_ai_bundle
from image_triage.ai_results import AICullBucket
from tests.harness import make_jpegs, open_folder, pump_until


def test_native_review_keeps_exact_decisions_and_nullable_scores(main_window, tmp_path):
    paths = make_jpegs(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    open_folder(main_window, tmp_path, 3)
    document = {"schema": "aiculler-decisions/1", "files": {
        path: {"decision": "keep" if i < 2 else "review", "capture": f"c{i}",
               "group": "burst", "comparison_set": "set-a", "rank_in_set": None,
               "score": None, "reasons": ["native reason"]}
        for i, path in enumerate(paths)}}
    main_window._ai_bundle = native_ai_bundle(document, tmp_path)
    main_window._ai_run.refresh_ai_state()
    main_window._ai_run.recompute_ai_demoted_burst_paths()
    assert main_window._ai_run._ai_demoted_burst_paths == set()
    groups = main_window._ai_run.ai_cull_record_groups()
    assert len(groups[AICullBucket.KEEPER]) == 2
    assert len(groups[AICullBucket.NEEDS_REVIEW]) == 1
    assert not groups[AICullBucket.AI_PICK]
    main_window.grid.set_current_index(0)
    main_window._inspector.update_inspector_context()
    assert main_window._ai_bundle.native_document == document


def test_analyze_uses_packaged_worker_and_reloads_catalog(main_window, tmp_path, monkeypatch):
    location = os.environ.get("CLI_CULLER_RUNTIME")
    if not location:
        pytest.skip("Set CLI_CULLER_RUNTIME to the assembled runtime manifest")
    monkeypatch.setenv("IMAGE_TRIAGE_CLI_CULLER_RUNTIME", location)
    paths = make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    open_folder(main_window, tmp_path, 2)
    monkeypatch.setattr(main_window._ai_run, "show_ai_review_complete_dialog", lambda **kwargs: None)
    monkeypatch.setattr(main_window._ai_run, "show_ai_review_progress_dialog", lambda **kwargs: None)
    main_window._settings.setValue("ai/use_cli_culler_native", True)
    main_window._settings.setValue("ai/use_legacy_culler", True)
    main_window._settings.setValue("ai/cli_culler_settings_json", "{}")
    try:
        main_window._ai_run.run_ai_pipeline()
        assert pump_until(lambda: main_window._active_ai_task is None and
                   main_window._ai_bundle is not None and main_window._ai_bundle.native_document is not None,
                   timeout=30)
        assert set(main_window._ai_bundle.native_document["files"]) == set(paths)
        assert (tmp_path / ".image_triage_ai/artifacts/cli_culler/active.json").exists()
        assert main_window._ai_bundle.native_document["settings"]["bursts_keep"] == 2
        main_window._ai_run.update_ai_toolbar_state()
        assert main_window.actions.run_ai_culling.isEnabled()
        assert main_window.actions.quick_rerank_ai_culling.isEnabled()
        assert main_window.actions.quick_rerank_ai_culling.text() == "Rerun CLI-Culler"
        assert not list(tmp_path.glob("*.xmp"))
        original = main_window._ai_bundle.native_document
        main_window._ai_bundle = None
        main_window._hidden_ai_results_checked_scope_key = ""
        main_window._ai_run.start_hidden_ai_results_load()
        assert pump_until(lambda: main_window._ai_bundle is not None, timeout=30)
        assert main_window._ai_bundle.native_document == original
        main_window.grid.set_current_index(0)
        main_window._inspector.update_inspector_context()
        assert main_window.grab().save(str(tmp_path / "native-review.png"))
    finally:
        main_window._settings.remove("ai/use_cli_culler_native")
        main_window._settings.remove("ai/use_legacy_culler")
        main_window._settings.remove("ai/cli_culler_settings_json")


def test_native_apply_never_recycles_a_manually_marked_winner(main_window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from image_triage.models import SessionAnnotation
    paths = make_jpegs(tmp_path, ["protected.jpg", "rejected.jpg"])
    open_folder(main_window, tmp_path, 2)
    document = {"schema": "aiculler-decisions/1", "files": {
        path: {"decision": "reject", "score": None} for path in paths}}
    main_window._ai_bundle = native_ai_bundle(document, tmp_path)
    main_window._annotations[paths[0]] = SessionAnnotation(winner=True)
    captured = {}

    def review(**kwargs):
        captured.update(kwargs)
        return QDialog(main_window)

    monkeypatch.setattr("image_triage.ai_run_controller.ApplyAIDecisionsDialog", review)
    monkeypatch.setattr(main_window, "_exec_dialog_with_geometry", lambda *args: QDialog.DialogCode.Rejected)
    main_window._ai_run.apply_ai_culling()
    assert [record.path for record in captured["reject_records"]] == [paths[1]]
    assert main_window._ai_bundle.native_document == document
    assert main_window._annotations[paths[0]].winner
