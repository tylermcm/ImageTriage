"""Characterization for WI-4.4 slice 3: batch-rename apply/dialog moved out of
MainWindow into image_triage.batch_rename_controller.BatchRenameApplyController.

The pure preview-building logic (build_batch_rename_preview) already has
coverage in tests/test_char_batch_plans.py. This file covers the async
apply path end-to-end through MainWindow (task kickoff -> progress dialog ->
_finalize_batch_rename rekeying records/annotations/undo), which previously
had zero test coverage at all.
"""
from __future__ import annotations

from pathlib import Path

from image_triage.batch_rename import BatchRenameRules, build_batch_rename_preview
from image_triage.batch_rename_controller import BatchRenameApplyController
from tests.harness import make_jpegs, open_folder, pump_until


def _listing(folder) -> list[str]:
    return sorted(p.name for p in Path(folder).iterdir() if p.is_file())


def test_apply_preview_renames_files_rekeys_records_and_pushes_undo(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    open_folder(main_window, tmp_path, 2)
    records = list(main_window._records)
    preview = build_batch_rename_preview(records, BatchRenameRules(prefix="trip_"))
    assert preview.can_apply

    started = main_window._batch_rename.apply_preview(preview, folder=str(tmp_path))
    assert started
    assert main_window._batch_rename.is_running

    assert pump_until(lambda: not main_window._batch_rename.is_running)

    assert _listing(tmp_path) == ["trip_a.jpg", "trip_b.jpg"]
    assert {record.path for record in main_window._records} == {
        str(tmp_path / "trip_a.jpg"),
        str(tmp_path / "trip_b.jpg"),
    }
    assert main_window._undo_stack
    assert main_window._undo_stack[-1].kind == "move"


def test_second_apply_while_one_is_running_is_rejected(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    open_folder(main_window, tmp_path, 2)
    preview = build_batch_rename_preview(main_window._records, BatchRenameRules(prefix="one_"))

    assert main_window._batch_rename.apply_preview(preview, folder=str(tmp_path))
    second_preview = build_batch_rename_preview(main_window._records, BatchRenameRules(prefix="two_"))
    assert not main_window._batch_rename.apply_preview(second_preview, folder=str(tmp_path))

    assert pump_until(lambda: not main_window._batch_rename.is_running)


def test_empty_preview_is_a_no_op() -> None:
    controller = BatchRenameApplyController(window=None)
    from image_triage.batch_rename import BatchRenamePreview

    empty_preview = BatchRenamePreview(
        items=(), planned_moves=(), renamed_count=0, unchanged_count=0, error_count=0, can_apply=False
    )
    assert controller.apply_preview(empty_preview, folder="C:/shots") is False
    assert not controller.is_running
