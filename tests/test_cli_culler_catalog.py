from types import SimpleNamespace
import json

from image_triage.ai_results import (
    load_ai_bundle, ai_cull_bucket_for_result, AICullBucket,
    refine_ai_result_with_review_insight, inspect_ai_bundle_source,
)
from image_triage.catalog import CatalogRepository
from image_triage.cli_culler_results import native_ai_bundle


def test_native_catalog_reload_preserves_every_field_and_null(tmp_path):
    path = str(tmp_path / "拍摄.jpg")
    document = {"schema": "aiculler-decisions/1", "future": [1, None],
                "settings": {"bursts_keep": 2}, "warnings": ["model absent"],
                "files": {path: {"decision": "keep", "score": None, "rank_in_set": None,
                                 "group": "burst-a", "capture": "capture-a", "scene": 1,
                                 "comparison_set": "scene-1", "reasons": ["protected"],
                                 "outputs": {"future": [1, 2]}}}}
    source = tmp_path / "decisions.json"
    source.write_text(json.dumps(document))
    bundle = load_ai_bundle(source)
    result = bundle.result_for_path(path)
    assert result.score is None and result.score_text == ""
    assert result.folder_percentile is None and result.normalized_score is None
    assert not result.is_top_pick and ai_cull_bucket_for_result(result) == AICullBucket.KEEPER
    assert refine_ai_result_with_review_insight(result, SimpleNamespace(detail_score=1)) is result
    repository = CatalogRepository(tmp_path / "catalog.sqlite")
    repository.save_folder_records(str(tmp_path), [])
    key = inspect_ai_bundle_source(source).cache_key
    assert repository.save_ai_bundle(str(tmp_path), cache_key=key, bundle=bundle)
    source.unlink()
    reloaded = CatalogRepository(tmp_path / "catalog.sqlite").load_ai_bundle(str(tmp_path), cache_key=key)
    assert reloaded.bundle.native_document == document
    restored = reloaded.bundle.result_for_path(path)
    assert restored.native_payload == document["files"][path]
    assert restored.score is None and restored.folder_percentile is None
    assert restored.confidence_bucket_label == "Keeper"
    assert "Engine decision: keep." in restored.explanation_lines


def test_two_native_burst_keepers_remain_keepers(tmp_path):
    document = {"schema": "aiculler-decisions/1", "files": {
        str(tmp_path / f"{i}.jpg"): {"decision": "keep", "group": "burst",
                                    "rank_in_set": i + 1, "score": i / 10}
        for i in range(2)}}
    bundle = native_ai_bundle(document, tmp_path)
    assert all(ai_cull_bucket_for_result(row) == AICullBucket.KEEPER
               and not row.is_top_pick for row in bundle.results_by_path.values())


def test_native_paths_preserve_distinct_case_and_accept_windows_aliases():
    from image_triage.ai_results import iter_ai_bundle_results
    document = {"schema": "aiculler-decisions/1", "files": {
        "/shoot/A.jpg": {"decision": "keep"}, "/shoot/a.jpg": {"decision": "reject"},
        "C:\\拍摄\\IMG.JPG": {"decision": "review"}}}
    bundle = native_ai_bundle(document, "decisions.json")
    assert len(iter_ai_bundle_results(bundle)) == 3
    assert bundle.result_for_path("/shoot/A.jpg").native_payload["decision"] == "keep"
    assert bundle.result_for_path("/shoot/a.jpg").native_payload["decision"] == "reject"
    assert bundle.result_for_path("c:/拍摄/img.jpg").native_payload["decision"] == "review"


def test_reopening_uses_the_latest_saved_provider_after_compatibility_runs(tmp_path):
    import os
    from image_triage.ai_workflow import build_ai_workflow_paths, existing_hidden_ai_report_dir
    paths = build_ai_workflow_paths(tmp_path)
    active = paths.artifacts_dir / "cli_culler/active.json"
    active.parent.mkdir(parents=True)
    active.write_text("{}")
    paths.report_dir.mkdir(parents=True)
    paths.ranked_export_path.write_text("legacy export")
    # Use representable modern times, separated well beyond filesystem
    # timestamp granularity (NTFS cannot distinguish 1 ns from 2 ns).
    epoch = 1_700_000_000
    os.utime(active, (epoch, epoch))
    os.utime(paths.ranked_export_path, (epoch + 10, epoch + 10))
    assert existing_hidden_ai_report_dir(tmp_path) == paths.report_dir
    os.utime(active, (epoch + 20, epoch + 20))
    assert existing_hidden_ai_report_dir(tmp_path) == active
