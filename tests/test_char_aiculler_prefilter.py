"""Characterization of the live pHash-only behaviour of AICullerRunTask (WI-2.4 d2).

These pin the pHash-only run behaviour: the stage
sequence the UI sees, and which images reach AI Culler ingest.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from image_triage.aiculler_workflow import (
    ALL_AICULLER_STAGES,
    AICullerRunTask,
    AICullerRuntime,
)
from image_triage.ai_workflow import AIWorkflowPaths
from image_triage.models import ImageRecord
from image_triage.phash_prefilter import (
    PHashPrefilterSettings,
    build_phash_prefilter_paths,
    run_phash_prefilter_from_signal_rows,
)


def _paths(photo_dir: Path) -> AIWorkflowPaths:
    hidden = photo_dir / ".image_triage_ai"
    report = hidden / "ranker_report"
    return AIWorkflowPaths(
        folder=photo_dir,
        hidden_root=hidden,
        artifacts_dir=hidden / "artifacts",
        report_dir=report,
        ranked_export_path=report / "ranked_clusters_export.csv",
        html_report_path=report / "ranked_clusters_report.html",
        semantic_export_path=report / "semantic_classifications.csv",
        semantic_summary_path=report / "semantic_summary.json",
    )


def _runtime(root: Path) -> AICullerRuntime:
    return AICullerRuntime(
        root=root,
        python_executable=root / "python.exe",
        cli_entrypoint=root / "aiculler" / "cli.py",
        clip_vision_model=root / "vision.onnx",
        clip_text_model=root / "text.onnx",
        tokenizer=root / "tokenizer.json",
    )


def _task(tmp_path: Path, names=("keeper.jpg", "duplicate.jpg"), **kwargs) -> tuple[AICullerRunTask, Path]:
    photo_dir = tmp_path / "photos"
    records = tuple(
        ImageRecord(path=str(photo_dir / name), name=name, size=1, modified_ns=1) for name in names
    )
    task = AICullerRunTask(
        folder=photo_dir,
        records=records,
        runtime=_runtime(tmp_path),
        paths=_paths(photo_dir),
        **kwargs,
    )
    return task, photo_dir


def _run_capturing_stages(task: AICullerRunTask) -> dict:
    stages: list[tuple[int, int, str]] = []
    outcome = {"finished": False, "failed": None, "phash_calls": 0, "commands": []}
    task.signals.stage.connect(lambda _folder, index, total, message: stages.append((index, total, message)))
    task.signals.finished.connect(lambda *_: outcome.__setitem__("finished", True))
    task.signals.failed.connect(lambda _folder, message: outcome.__setitem__("failed", message))

    def fake_phash():
        outcome["phash_calls"] += 1
        return {"rows": 0, "decisions": 0}

    with (
        patch.object(AICullerRuntime, "validate"),
        patch.object(task, "_run_phash_prefilter", side_effect=fake_phash),
        patch.object(task, "_run_command", side_effect=lambda command, stage_message: outcome["commands"].append(stage_message)),
        patch.object(task, "_prune_aiculler_db_to_include_file"),
        patch.object(task, "_write_gui_exports"),
        patch("image_triage.aiculler_workflow.compute_and_store_winner_scores"),
    ):
        task.run()
    outcome["stages"] = stages
    return outcome


def test_phash_enabled_run_adds_one_duplicate_stage_before_ingest(tmp_path) -> None:
    task, _ = _task(tmp_path, phash_prefilter_settings=PHashPrefilterSettings(enabled=True))

    outcome = _run_capturing_stages(task)

    assert outcome["failed"] is None and outcome["finished"]
    total = len(ALL_AICULLER_STAGES) + 2
    messages = [message for _index, _total, message in outcome["stages"]]
    assert messages[0] == "Finding duplicates"
    assert messages[-1] == "Preparing GUI results"
    assert [index for index, _t, _m in outcome["stages"]] == list(range(1, total + 1))
    assert {t for _i, t, _m in outcome["stages"]} == {total}
    assert outcome["phash_calls"] == 1
    assert len(outcome["commands"]) == len(ALL_AICULLER_STAGES)


def test_phash_disabled_run_has_no_duplicate_stage(tmp_path) -> None:
    task, _ = _task(tmp_path, phash_prefilter_settings=PHashPrefilterSettings(enabled=False))

    outcome = _run_capturing_stages(task)

    total = len(ALL_AICULLER_STAGES) + 1
    messages = [message for _i, _t, message in outcome["stages"]]
    assert "Finding duplicates" not in messages
    assert messages[-1] == "Preparing GUI results"
    assert {t for _i, t, _m in outcome["stages"]} == {total}
    assert outcome["phash_calls"] == 0


def test_prefilter_stage_flags_require_ingest_and_the_enabled_setting(tmp_path) -> None:
    on = PHashPrefilterSettings(enabled=True)
    task, _ = _task(tmp_path, phash_prefilter_settings=on)
    assert task._phash_stage_enabled() is True

    without_ingest, _ = _task(tmp_path, stages=("rank",), phash_prefilter_settings=on)
    assert without_ingest._phash_stage_enabled() is False

    off, _ = _task(tmp_path, run_phash_prefilter=False, phash_prefilter_settings=on)
    assert off._phash_stage_enabled() is False


def test_include_file_scopes_ingest_to_one_representative_per_record(tmp_path) -> None:
    task, photo_dir = _task(tmp_path, phash_prefilter_settings=PHashPrefilterSettings(enabled=False))

    include_path = task._write_aiculler_include_file()

    assert include_path is not None and include_path.name == "aiculler_include_paths.txt"
    assert include_path.read_text(encoding="utf-8").split() == [
        str(photo_dir / "keeper.jpg"),
        str(photo_dir / "duplicate.jpg"),
    ]


def test_include_file_drops_images_the_phash_prefilter_removed(tmp_path) -> None:
    settings = PHashPrefilterSettings(enabled=True, hamming_threshold=0)
    task, photo_dir = _task(tmp_path, phash_prefilter_settings=settings)
    run_phash_prefilter_from_signal_rows(
        (
            {"file_path": str(photo_dir / "keeper.jpg"), "phash_duplicate_score": "0.0", "best_representative": "1"},
            {"file_path": str(photo_dir / "duplicate.jpg"), "phash_duplicate_score": "1.0", "best_representative": "0"},
        ),
        settings=settings,
        paths=build_phash_prefilter_paths(task.paths),
    )

    include_path = task._write_aiculler_include_file()

    assert include_path is not None
    assert include_path.read_text(encoding="utf-8").split() == [str(photo_dir / "keeper.jpg")]


def test_include_file_is_none_when_nothing_is_left(tmp_path) -> None:
    task, _ = _task(tmp_path, names=(), phash_prefilter_settings=PHashPrefilterSettings(enabled=False))

    assert task._write_aiculler_include_file() is None
