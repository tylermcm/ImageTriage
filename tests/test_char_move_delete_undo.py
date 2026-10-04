"""Characterization of move / delete / undo on the real MainWindow (WI-0.5)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QDialog

from image_triage.ai_results import AIBundle, AICullBucket
from image_triage.file_ops import FileMove
from image_triage.models import DeleteMode, WinnerMode
from image_triage.transfer_progress import TransferResult
from image_triage.ui.apply_ai_decisions_dialog import ApplyAIDecisionsDialog
from tests.harness import make_jpegs, open_folder, pump_until


@pytest.fixture
def window(main_window, tmp_path):
    recycle = tmp_path / "_recycle_sandbox"
    main_window._winner_mode = WinnerMode.LOGICAL
    main_window._delete_mode = DeleteMode.SAFE_TRASH
    with patch.object(main_window, "_recycle_root_for_folder", lambda folder=None: recycle):
        yield main_window


def _names(window) -> list[str]:
    return sorted(Path(record.path).name for record in window._records)


def test_move_relocates_the_file_removes_the_record_and_records_undo(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    dest = tmp_path / "dest"

    moved = window._move_records_by_paths([a], str(dest))

    assert moved == 1
    assert not os.path.exists(a) and (dest / "a.jpg").exists()
    assert _names(window) == ["b.jpg"]
    action = window._undo_stack[-1]
    assert action.kind == "move" and action.primary_path == a
    assert [(m.source_path, m.target_path) for m in action.file_moves] == [(a, str(dest / "a.jpg"))]


def test_move_carries_the_annotation_to_the_new_path(window, tmp_path) -> None:
    source = tmp_path / "src"
    (a,) = make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)
    window._toggle_winner(0, advance_override=False)
    dest = tmp_path / "dest"

    window._move_records_by_paths([a], str(dest))

    moved_path = str(dest / "a.jpg")
    assert a not in window._annotations
    assert window._annotations[moved_path].winner


def test_undo_move_puts_the_file_back_and_reloads_the_folder(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    window._move_records_by_paths([a], str(tmp_path / "dest"))

    window._undo_last_action()

    assert os.path.exists(a) and not (tmp_path / "dest" / "a.jpg").exists()
    assert not window._undo_stack


def test_move_into_an_unusable_destination_warns_and_keeps_the_record(window, dialogs, tmp_path) -> None:
    source = tmp_path / "src"
    (a,) = make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a folder")

    moved = window._move_records_by_paths([a], str(blocker / "sub"))

    assert moved == 0
    assert os.path.exists(a) and _names(window) == ["a.jpg"]
    assert not window._undo_stack
    assert any(title == "Move Failed" for _, title, _ in dialogs.messages)


def test_move_ignores_paths_that_are_not_in_the_current_view(window, tmp_path) -> None:
    source = tmp_path / "src"
    make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)

    assert window._move_records_by_paths([str(tmp_path / "nope.jpg")], str(tmp_path / "dest")) == 0
    assert not (tmp_path / "dest").exists()


def test_safe_delete_moves_to_recycle_and_undo_restores(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)

    window._delete_record(0)

    deleted = a if not os.path.exists(a) else b
    assert not os.path.exists(deleted)
    recycled = list((tmp_path / "_recycle_sandbox").glob("*.jpg"))
    assert [p.name for p in recycled] == [Path(deleted).name]
    assert len(window._records) == 1
    assert window._undo_stack[-1].kind == "delete"

    window._undo_last_action()

    assert os.path.exists(deleted)
    assert not list((tmp_path / "_recycle_sandbox").glob("*.jpg"))


def test_delete_drops_the_annotation(window, tmp_path) -> None:
    source = tmp_path / "src"
    make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)
    record = window._records[0]
    window._toggle_winner(0, advance_override=False)
    assert record.path in window._annotations

    window._delete_record(0)

    assert record.path not in window._annotations


def test_delete_failure_keeps_the_file_and_warns(window, dialogs, tmp_path) -> None:
    source = tmp_path / "src"
    (a,) = make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)

    with patch("image_triage.window.shutil.move", side_effect=OSError("locked")):
        window._delete_record(0)

    assert os.path.exists(a)
    assert len(window._records) == 1 and not window._undo_stack
    assert any(title == "Delete Failed" for _, title, _ in dialogs.messages)


def test_undo_is_last_in_first_out(window, tmp_path) -> None:
    source = tmp_path / "src"
    make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    window._toggle_winner(0, advance_override=False)
    window._toggle_reject(1, advance_override=False)
    kinds = [action.kind for action in window._undo_stack]
    assert kinds == ["annotation", "annotation"]
    first_path = window._undo_stack[0].primary_path

    window._undo_last_action()

    assert len(window._undo_stack) == 1
    assert window._undo_stack[0].primary_path == first_path


def test_copy_records_leaves_the_source_and_creates_the_copy(window, tmp_path) -> None:
    source = tmp_path / "src"
    (a,) = make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)

    copied = window._copy_records_by_paths([a], str(tmp_path / "dest"))

    assert copied == 1
    assert os.path.exists(a) and (tmp_path / "dest" / "a.jpg").exists()
    assert _names(window) == ["a.jpg"]


def test_copying_a_batch_goes_through_the_progress_and_cancel_transfer(window, tmp_path) -> None:
    """WI-4.7: Copy used to be a plain per-file loop with no progress dialog
    at all. It now routes through the same `run_file_transfer` mechanism
    Move already used (WI-4.1b), with `keep_source=True`."""
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    dest = tmp_path / "dest"

    with patch("image_triage.record_ops_controller.run_file_transfer") as mock_transfer:
        mock_transfer.return_value = TransferResult(
            moved={
                0: (FileMove(source_path=a, target_path=str(dest / "a.jpg")),),
                1: (FileMove(source_path=b, target_path=str(dest / "b.jpg")),),
            }
        )
        copied = window._copy_records_by_paths([a, b], str(dest))

    assert copied == 2
    assert mock_transfer.call_args.kwargs["keep_source"] is True
    # The source records are never removed from the view by a copy.
    assert _names(window) == ["a.jpg", "b.jpg"]


def test_copy_failure_reports_a_warning_and_counts_only_the_successes(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    dest = tmp_path / "dest"

    with patch("image_triage.record_ops_controller.run_file_transfer") as mock_transfer, patch(
        "image_triage.record_ops_controller.QMessageBox"
    ) as mock_box:
        mock_transfer.return_value = TransferResult(
            moved={0: (FileMove(source_path=a, target_path=str(dest / "a.jpg")),)},
            failed={1: "disk full"},
        )
        copied = window._copy_records_by_paths([a, b], str(dest))

    assert copied == 1
    mock_box.warning.assert_called_once()


# ---- WI-4.7: confirm -> transfer -> completion for To Folder / To Recent --


def test_declining_the_confirmation_makes_no_file_changes(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    dest = tmp_path / "dest"

    with patch("image_triage.record_ops_controller.confirm_transfer", return_value=(False, True)) as mock_confirm:
        window._record_ops._confirm_and_transfer(list(window._records), str(dest), mode="copy")

    mock_confirm.assert_called_once()
    assert not dest.exists()
    assert _names(window) == ["a.jpg", "b.jpg"]


def test_accepting_the_confirmation_runs_the_transfer_and_remembers_the_companions_choice(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    dest = tmp_path / "dest"

    with patch("image_triage.record_ops_controller.confirm_transfer", return_value=(True, False)), patch(
        "image_triage.record_ops_controller.show_transfer_complete"
    ) as mock_complete:
        window._record_ops._confirm_and_transfer(list(window._records), str(dest), mode="copy")

    assert (dest / "a.jpg").exists() and (dest / "b.jpg").exists()
    assert window._settings.value(window.TRANSFER_INCLUDE_COMPANIONS_KEY, True, bool) is False
    mock_complete.assert_called_once()
    assert mock_complete.call_args.kwargs["count"] == 2


def test_unchecking_include_companions_transfers_only_the_primary_file(window, tmp_path) -> None:
    source = tmp_path / "src"
    (raw,) = make_jpegs(source, ["img.jpg"])
    companion = source / "img.xmp"
    companion.write_text("sidecar")
    open_folder(window, source, 1)
    record = window._records[0]
    object.__setattr__(record, "companion_paths", (str(companion),))
    dest = tmp_path / "dest"

    with patch("image_triage.record_ops_controller.confirm_transfer", return_value=(True, False)):
        window._record_ops.copy_selected_records_to_destination(str(dest))

    assert (dest / "img.jpg").exists()
    assert not (dest / "img.xmp").exists(), "unchecked companions must not be transferred"
    assert companion.exists(), "the sidecar stays untouched at the source"


def test_completion_dialog_is_skipped_for_a_single_file(window, tmp_path) -> None:
    source = tmp_path / "src"
    (a,) = make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)
    dest = tmp_path / "dest"

    with patch("image_triage.record_ops_controller.confirm_transfer", return_value=(True, True)), patch(
        "image_triage.record_ops_controller.show_transfer_complete"
    ) as mock_complete:
        window._record_ops.copy_selected_records_to_destination(str(dest))

    mock_complete.assert_not_called()


def test_moving_a_batch_no_longer_rebuilds_the_view_once_per_record(window, tmp_path) -> None:
    """N9/WI-4.1b, fixed: `_move_records_by_paths` used to call
    `_remove_record` once per moved item (each a full `_apply_records_view`
    rebuild), so a rebuild count that scaled with the batch size. It now
    routes through `_remove_records_by_paths`, one rebuild for the whole
    batch. There is still a little unrelated noise here — `run_file_transfer`
    spins a nested `QEventLoop` while its worker thread runs, and the
    directory watcher can notice the app's own file moves and queue its own
    refresh — so the count isn't pinned at exactly 1. What matters, and is
    safe to pin, is that it no longer scales with the number of files moved:
    moving 10 records here is still a small, constant number of rebuilds,
    not 10 (or more) of them."""
    source = tmp_path / "src"
    paths = make_jpegs(source, [f"{letter}.jpg" for letter in "abcdefghij"])
    open_folder(window, source, 10)
    dest = tmp_path / "dest"

    real_apply_records_view = window._apply_records_view
    calls: list[None] = []

    def counting_apply_records_view(*args, **kwargs):
        calls.append(None)
        return real_apply_records_view(*args, **kwargs)

    with patch.object(window, "_apply_records_view", side_effect=counting_apply_records_view):
        moved = window._move_records_by_paths(list(paths), str(dest))

    assert moved == 10
    assert len(calls) < moved // 2, (
        f"rebuild count should stay well under the batch size (old behaviour was ~1 per file), got {len(calls)}"
    )


def test_moving_multiple_records_pushes_one_undo_action_per_record_sharing_a_batch_id(window, tmp_path) -> None:
    """`_move_records_by_paths` still pushes one UndoAction per moved record
    (each needs its own file_moves/primary_path), but they now share a
    batch_id so a single Undo reverses the whole batch — see
    `test_undoing_a_batch_move_reverses_every_file_in_one_undo`."""
    source = tmp_path / "src"
    a, b, c = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg"])
    open_folder(window, source, 3)
    dest = tmp_path / "dest"

    moved = window._move_records_by_paths([a, b, c], str(dest))

    assert moved == 3
    assert [action.kind for action in window._undo_stack] == ["move", "move", "move"]
    assert {action.primary_path for action in window._undo_stack} == {a, b, c}
    assert _names(window) == []
    batch_ids = {action.batch_id for action in window._undo_stack}
    assert len(batch_ids) == 1 and next(iter(batch_ids))


def test_undoing_a_batch_move_reverses_every_file_in_one_undo(window, tmp_path) -> None:
    """The user-facing behaviour change: one Undo click reverses an entire
    batch move (previously needed one click per file), and only one folder
    reload happens for the whole batch, not one per file."""
    source = tmp_path / "src"
    a, b, c = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg"])
    open_folder(window, source, 3)
    dest = tmp_path / "dest"

    moved = window._move_records_by_paths([a, b, c], str(dest))
    assert moved == 3
    assert pump_until(lambda: len(window._records) == 0)

    real_load_folder = window._load_folder
    load_folder_calls: list[None] = []

    def counting_load_folder(*args, **kwargs):
        load_folder_calls.append(None)
        return real_load_folder(*args, **kwargs)

    with patch.object(window, "_load_folder", side_effect=counting_load_folder):
        window._undo_last_action()

    assert window._undo_stack == []
    assert len(load_folder_calls) == 1
    assert pump_until(lambda: len(window._records) == 3)
    assert os.path.exists(a) and os.path.exists(b) and os.path.exists(c)


def test_move_records_by_paths_with_explicit_batch_id_joins_that_batch(window, tmp_path) -> None:
    """WI-6.2: `_apply_ai_culling` needs its AI-Pick move to join the same
    Undo batch as its Reject/recycle half, so `move_records_by_paths` (and
    the `_move_records_by_paths` wrapper) accept an optional `batch_id` that,
    when given, is used as-is instead of minting a fresh one."""
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    dest = tmp_path / "dest"

    moved = window._move_records_by_paths([a, b], str(dest), batch_id="given-batch-id")

    assert moved == 2
    assert {action.batch_id for action in window._undo_stack} == {"given-batch-id"}


def test_move_records_by_paths_without_batch_id_still_mints_its_own(window, tmp_path) -> None:
    """Every other existing caller (drag-drop, the regular batch-move action)
    calls this without a batch_id and must be completely unaffected: it still
    gets a freshly minted id per call, same as before this work item. (The
    other pre-existing tests in this file that call `_move_records_by_paths`
    with no `batch_id` argument at all are the real proof of this -- they
    pass unmodified -- this test just pins the "fresh id, not empty/shared"
    behaviour explicitly.)"""
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    dest = tmp_path / "dest"

    moved = window._move_records_by_paths([a, b], str(dest))

    assert moved == 2
    batch_ids = {action.batch_id for action in window._undo_stack}
    assert len(batch_ids) == 1
    minted = next(iter(batch_ids))
    assert minted and minted != "given-batch-id"


# ---- WI-4.1b: _remove_records_by_paths, the new batch removal API --------

def test_remove_records_by_paths_refreshes_the_view_exactly_once(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b, c, d = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    open_folder(window, source, 4)

    real_apply_records_view = window._apply_records_view
    calls: list[None] = []

    def counting_apply_records_view(*args, **kwargs):
        calls.append(None)
        return real_apply_records_view(*args, **kwargs)

    with patch.object(window, "_apply_records_view", side_effect=counting_apply_records_view):
        removed = window._record_ops.remove_records_by_paths([a, c])

    assert removed == 2
    assert len(calls) == 1
    assert _names(window) == ["b.jpg", "d.jpg"]


def test_remove_records_by_paths_focuses_the_next_surviving_record(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b, c, d = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    open_folder(window, source, 4)

    window._record_ops.remove_records_by_paths([a, b])

    assert window._records_view.current_visible_record_path() == c


def test_remove_records_by_paths_falls_back_to_the_nearest_earlier_survivor(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b, c, d = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    open_folder(window, source, 4)

    window._record_ops.remove_records_by_paths([c, d])

    assert window._records_view.current_visible_record_path() == b


def test_remove_records_by_paths_removing_everything_does_not_crash(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)

    removed = window._record_ops.remove_records_by_paths([a, b])

    assert removed == 2
    assert _names(window) == []


def test_remove_records_by_paths_ignores_paths_not_in_the_current_view(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)

    removed = window._record_ops.remove_records_by_paths([a, str(tmp_path / "nonexistent.jpg")])

    assert removed == 1
    assert _names(window) == ["b.jpg"]


def test_remove_records_by_paths_with_nothing_to_remove_is_a_no_op(window, tmp_path) -> None:
    source = tmp_path / "src"
    make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)

    real_apply_records_view = window._apply_records_view
    calls: list[None] = []
    with patch.object(window, "_apply_records_view", side_effect=lambda *a, **k: calls.append(None)):
        removed = window._record_ops.remove_records_by_paths([str(tmp_path / "nonexistent.jpg")])

    assert removed == 0
    assert calls == []


# ---- WI-4.1b: _apply_ai_culling's per-item movers with deferred removal ---
# (`_apply_ai_culling` itself needs a real AI bundle/cull-bucket classification
# to exercise end-to-end, which is a separate, pre-existing gap (N2); these
# tests cover the new `defer_removal` mechanism it now relies on directly,
# since that's the actual new, risk-bearing code from this work item.)

def test_move_record_to_path_with_defer_removal_keeps_the_record_visible(window, tmp_path) -> None:
    source = tmp_path / "src"
    (a,) = make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)
    dest = tmp_path / "_winners"

    ok = window._record_ops.move_record_to_path(a, str(dest), defer_removal=True)

    assert ok is True
    assert not os.path.exists(a) and (dest / "a.jpg").exists()
    assert _names(window) == ["a.jpg"], "record stays in the view until the caller batch-removes it"
    assert len(window._undo_stack) == 1 and window._undo_stack[0].kind == "move"


def test_move_record_to_ai_recycle_by_path_with_defer_removal_keeps_the_record_visible(window, tmp_path) -> None:
    source = tmp_path / "src"
    (a,) = make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)

    ok = window._record_ops.move_record_to_ai_recycle_by_path(a, defer_removal=True)

    assert ok is True
    assert not os.path.exists(a)
    assert _names(window) == ["a.jpg"], "record stays in the view until the caller batch-removes it"
    assert len(window._undo_stack) == 1 and window._undo_stack[0].kind == "delete"


def test_deferred_movers_compose_with_the_batch_removal_api(window, tmp_path) -> None:
    """The exact pattern `_apply_ai_culling` now uses: move/recycle several
    records with removal deferred, then remove them all in one refresh."""
    source = tmp_path / "src"
    a, b, c = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg"])
    open_folder(window, source, 3)
    winners_dir = tmp_path / "_winners"

    real_apply_records_view = window._apply_records_view
    calls: list[None] = []

    def counting_apply_records_view(*args, **kwargs):
        calls.append(None)
        return real_apply_records_view(*args, **kwargs)

    with patch.object(window, "_apply_records_view", side_effect=counting_apply_records_view):
        moved = window._record_ops.move_record_to_path(a, str(winners_dir), defer_removal=True)
        recycled = window._record_ops.move_record_to_ai_recycle_by_path(b, defer_removal=True)
        assert calls == [], "no rebuild yet while removal is deferred"
        removed = window._record_ops.remove_records_by_paths([a, b])

    assert moved is True and recycled is True
    assert removed == 2
    assert len(calls) == 1
    assert [action.kind for action in window._undo_stack] == ["move", "delete"]
    assert _names(window) == ["c.jpg"]


def test_apply_ai_cullings_mixed_move_and_recycle_batch_undoes_together(window, tmp_path) -> None:
    """`_apply_ai_culling` shares one batch_id across its AI Pick moves
    (kind="move") and Reject recycles (kind="delete") in the same call, so
    the whole "Apply AI Decisions" action reverses in one Undo, mixed kinds
    and all."""
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    winners_dir = tmp_path / "_winners"
    batch_id = "shared-ai-cull-batch"

    moved = window._record_ops.move_record_to_path(a, str(winners_dir), defer_removal=True, batch_id=batch_id)
    recycled = window._record_ops.move_record_to_ai_recycle_by_path(b, defer_removal=True, batch_id=batch_id)
    window._record_ops.remove_records_by_paths([a, b])

    assert moved is True and recycled is True
    assert [action.batch_id for action in window._undo_stack] == [batch_id, batch_id]
    assert pump_until(lambda: len(window._records) == 0)

    window._undo_last_action()

    assert window._undo_stack == []
    assert pump_until(lambda: len(window._records) == 2)
    assert os.path.exists(a) and os.path.exists(b)


def test_undoing_past_an_already_empty_batch_is_a_safe_no_op(window, tmp_path) -> None:
    """A user pressing Undo again after a batch is already fully reversed
    (nothing left on the stack, or a non-batch action follows) shouldn't
    error or double-reload. Historical context: this scenario used to
    require exactly N undo clicks for an N-file batch, each triggering its
    own full-folder reload (`_load_folder`); grouped undo (one click per
    batch) makes over-clicking Undo far less likely, but it should still be
    harmless if it happens."""
    source = tmp_path / "src"
    (a,) = make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)
    dest = tmp_path / "dest"

    moved = window._move_records_by_paths([a], str(dest))
    assert moved == 1
    assert pump_until(lambda: len(window._records) == 0)

    window._undo_last_action()
    assert pump_until(lambda: len(window._records) == 1)
    assert window._undo_stack == []

    window._undo_last_action()  # nothing left to undo
    assert window._undo_stack == []
    assert len(window._records) == 1


# ---- WI-6.2: Apply AI Decisions -- thumbnail-grid confirmation + routing the
# winners move through run_file_transfer -----------------------------------

def _set_ai_cull_groups(
    window,
    *,
    ai_pick: list | None = None,
    reject: list | None = None,
    keeper: list | None = None,
    review: list | None = None,
):
    """Bypasses the real AI bundle/cull-bucket classification (a separate,
    pre-existing concern) and hands `_apply_ai_culling` a controlled set of
    groups directly, the same way the WI-4.1b tests above stub out the
    pieces they don't need to exercise end to end."""
    # Just needs to be non-None (and a real AIBundle, since `_update_action_states`
    # and friends look up `.results_by_path` on it mid-move) to pass the guard --
    # the real cull-bucket classification is a separate, pre-existing concern.
    window._ai_bundle = AIBundle(source_path="", export_csv_path="")
    groups = {
        AICullBucket.AI_PICK: list(ai_pick or []),
        AICullBucket.REJECT: list(reject or []),
        AICullBucket.KEEPER: list(keeper or []),
        AICullBucket.NEEDS_REVIEW: list(review or []),
    }
    return patch.object(window._ai_run, "ai_cull_record_groups", return_value=groups)


def _accept_apply_ai_dialog(window):
    """Stands in for the user clicking Apply: same seam
    (`_exec_dialog_with_geometry`) every other in-house confirmation dialog
    in this codebase is tested through, since `QDialog.exec()` is blocked in
    this headless harness."""
    captured: dict[str, ApplyAIDecisionsDialog] = {}

    def _fake_exec(dialog, _dialog_id):
        captured["dialog"] = dialog
        return QDialog.DialogCode.Accepted

    return patch.object(window, "_exec_dialog_with_geometry", side_effect=_fake_exec), captured


def _reject_apply_ai_dialog(window):
    return patch.object(window, "_exec_dialog_with_geometry", return_value=QDialog.DialogCode.Rejected)


def test_apply_ai_decisions_shows_the_dialog_with_the_right_records_in_each_group(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b, c, d, e = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"])
    open_folder(window, source, 5)
    by_path = {record.path: record for record in window._records}

    with _set_ai_cull_groups(
        window,
        ai_pick=[by_path[a], by_path[b]],
        reject=[by_path[c]],
        keeper=[by_path[d]],
        review=[by_path[e]],
    ):
        patch_exec, captured = _accept_apply_ai_dialog(window)
        with patch_exec:
            window._ai_run.apply_ai_culling()

    dialog = captured["dialog"]
    assert {record.path for record in dialog.ai_pick_records} == {a, b}
    assert {record.path for record in dialog.reject_records} == {c}
    assert dialog.keeper_count == 1
    assert dialog.review_count == 1


def test_declining_the_apply_ai_decisions_dialog_makes_no_file_changes(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)
    by_path = {record.path: record for record in window._records}

    with _set_ai_cull_groups(window, ai_pick=[by_path[a]], reject=[by_path[b]]):
        with _reject_apply_ai_dialog(window):
            window._ai_run.apply_ai_culling()

    assert os.path.exists(a) and os.path.exists(b)
    assert _names(window) == ["a.jpg", "b.jpg"]
    assert not window._undo_stack
    assert not (tmp_path / "_recycle_sandbox").exists() or not list(
        (tmp_path / "_recycle_sandbox").glob("*.jpg")
    )


def test_accepting_apply_ai_decisions_moves_winners_and_recycles_rejects_in_one_undo_batch(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b, c = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg"])
    open_folder(window, source, 3)
    by_path = {record.path: record for record in window._records}
    winners_dir = source / "_winners"

    with _set_ai_cull_groups(window, ai_pick=[by_path[a]], reject=[by_path[b]], keeper=[by_path[c]]):
        patch_exec, _captured = _accept_apply_ai_dialog(window)
        with patch_exec:
            window._ai_run.apply_ai_culling()

    assert not os.path.exists(a) and (winners_dir / "a.jpg").exists()
    assert not os.path.exists(b)
    recycled = list((tmp_path / "_recycle_sandbox").glob("*.jpg"))
    assert [p.name for p in recycled] == ["b.jpg"]
    assert os.path.exists(c), "Keeper images are not moved by Apply AI Decisions"

    assert [action.kind for action in window._undo_stack] == ["move", "delete"]
    batch_ids = {action.batch_id for action in window._undo_stack}
    assert len(batch_ids) == 1 and next(iter(batch_ids))
    assert pump_until(lambda: len(window._records) == 1)

    window._undo_last_action()

    assert window._undo_stack == []
    # The reload picks up the (now-empty) "_winners" folder left behind on
    # disk as its own record alongside the three restored images -- that
    # leftover-empty-folder behaviour predates this work item and is
    # unrelated to it, so this just accounts for it rather than re-litigating
    # it.
    assert pump_until(lambda: len({Path(r.path).name for r in window._records if not r.is_folder}) == 3)
    assert {Path(r.path).name for r in window._records if not r.is_folder} == {"a.jpg", "b.jpg", "c.jpg"}
    assert os.path.exists(a) and os.path.exists(b)
    assert not list((tmp_path / "_recycle_sandbox").glob("*.jpg"))


def test_apply_ai_decisions_cancel_partway_through_winners_move_leaves_the_rest_untouched(
    window, tmp_path
) -> None:
    """A user can cancel the winners move's progress dialog partway through a
    multi-file Apply AI Decisions run. `move_records_by_paths` only pushes
    undo entries and removes records for the subset `run_file_transfer`
    actually reports as moved, so this should naturally do the right thing:
    the moved subset is gone from the view and undoable, the rest stays on
    disk and in the grid untouched, and the Reject loop / follow-up status
    message still run normally afterwards without double-counting anything."""
    source = tmp_path / "src"
    a, b, c = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg"])
    d, = make_jpegs(source, ["d.jpg"])
    open_folder(window, source, 4)
    by_path = {record.path: record for record in window._records}
    winners_dir = source / "_winners"
    winners_dir.mkdir(parents=True, exist_ok=True)

    # Simulate the real TransferWorker: "a" finished moving before the user
    # hit Cancel; "b" and "c" were never touched.
    moved_target = str(winners_dir / "a.jpg")
    shutil.move(a, moved_target)
    fake_result = TransferResult(
        moved={0: (FileMove(source_path=a, target_path=moved_target),)},
        failed={},
        cancelled=True,
    )

    with _set_ai_cull_groups(
        window, ai_pick=[by_path[a], by_path[b], by_path[c]], reject=[by_path[d]]
    ):
        patch_exec, _captured = _accept_apply_ai_dialog(window)
        with patch_exec, patch(
            "image_triage.record_ops_controller.run_file_transfer", return_value=fake_result
        ):
            window._ai_run.apply_ai_culling()
        # Captured immediately: the directory watcher can overwrite the
        # status bar with its own "Detected folder changes" refresh message
        # once the event loop gets pumped again (see the pump_until calls
        # below), which is unrelated noise, not something this feature sets.
        status_message = window.statusBar().currentMessage()

    # "a" moved: gone from disk at its old path, present in _winners, removed
    # from the grid, and undoable.
    assert not os.path.exists(a)
    assert os.path.exists(moved_target)
    move_actions = [action for action in window._undo_stack if action.kind == "move"]
    assert len(move_actions) == 1 and move_actions[0].primary_path == a

    # "b" and "c" were never part of the cancelled transfer: still on disk at
    # their original paths, still shown in the grid, not touched or counted.
    assert os.path.exists(b) and os.path.exists(c)
    assert pump_until(lambda: len(window._records) == 2)
    names = set(_names(window))
    assert names == {"b.jpg", "c.jpg"}

    # The Reject loop (an unrelated record, "d") still ran to completion and
    # shares the same undo batch as the one successfully-moved winner.
    delete_actions = [action for action in window._undo_stack if action.kind == "delete"]
    assert len(delete_actions) == 1 and delete_actions[0].primary_path == d
    assert not os.path.exists(d)
    recycled = list((tmp_path / "_recycle_sandbox").glob("*.jpg"))
    assert [p.name for p in recycled] == ["d.jpg"]

    batch_ids = {action.batch_id for action in window._undo_stack}
    assert len(batch_ids) == 1 and next(iter(batch_ids)), "winners + reject halves share one Undo batch"

    # Status bar reflects only what actually moved -- no double counting of
    # the two files that never got there.
    assert "moved 1 AI Pick image(s)" in status_message
    assert "1 Reject image(s)" in status_message

    # Undo reverses the whole shared batch in one click, including the
    # cancelled-but-partially-applied winners half and the reject half.
    window._undo_last_action()

    assert window._undo_stack == []
    assert pump_until(lambda: len({Path(r.path).name for r in window._records if not r.is_folder}) == 4)
    assert {Path(r.path).name for r in window._records if not r.is_folder} == {
        "a.jpg",
        "b.jpg",
        "c.jpg",
        "d.jpg",
    }
    assert os.path.exists(a) and os.path.exists(b) and os.path.exists(c) and os.path.exists(d)
    assert not list((tmp_path / "_recycle_sandbox").glob("*.jpg"))
