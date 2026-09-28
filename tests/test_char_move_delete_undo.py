"""Characterization of move / delete / undo on the real MainWindow (WI-0.5)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from image_triage.models import DeleteMode, WinnerMode
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


def test_moving_a_batch_no_longer_rebuilds_the_view_once_per_record(window, tmp_path) -> None:
    """N9/WI-4.1b, fixed: `_move_records_by_paths` used to call
    `_remove_record` once per moved item (each a full `_apply_records_view`
    rebuild), so a rebuild count that scaled with the batch size. It now
    routes through `_remove_records_by_paths`, one rebuild for the whole
    batch. There is still a little unrelated noise here — `run_move_transfer`
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
        removed = window._remove_records_by_paths([a, c])

    assert removed == 2
    assert len(calls) == 1
    assert _names(window) == ["b.jpg", "d.jpg"]


def test_remove_records_by_paths_focuses_the_next_surviving_record(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b, c, d = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    open_folder(window, source, 4)

    window._remove_records_by_paths([a, b])

    assert window._current_visible_record_path() == c


def test_remove_records_by_paths_falls_back_to_the_nearest_earlier_survivor(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b, c, d = make_jpegs(source, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    open_folder(window, source, 4)

    window._remove_records_by_paths([c, d])

    assert window._current_visible_record_path() == b


def test_remove_records_by_paths_removing_everything_does_not_crash(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)

    removed = window._remove_records_by_paths([a, b])

    assert removed == 2
    assert _names(window) == []


def test_remove_records_by_paths_ignores_paths_not_in_the_current_view(window, tmp_path) -> None:
    source = tmp_path / "src"
    a, b = make_jpegs(source, ["a.jpg", "b.jpg"])
    open_folder(window, source, 2)

    removed = window._remove_records_by_paths([a, str(tmp_path / "nonexistent.jpg")])

    assert removed == 1
    assert _names(window) == ["b.jpg"]


def test_remove_records_by_paths_with_nothing_to_remove_is_a_no_op(window, tmp_path) -> None:
    source = tmp_path / "src"
    make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)

    real_apply_records_view = window._apply_records_view
    calls: list[None] = []
    with patch.object(window, "_apply_records_view", side_effect=lambda *a, **k: calls.append(None)):
        removed = window._remove_records_by_paths([str(tmp_path / "nonexistent.jpg")])

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

    ok = window._move_record_to_path(a, str(dest), defer_removal=True)

    assert ok is True
    assert not os.path.exists(a) and (dest / "a.jpg").exists()
    assert _names(window) == ["a.jpg"], "record stays in the view until the caller batch-removes it"
    assert len(window._undo_stack) == 1 and window._undo_stack[0].kind == "move"


def test_move_record_to_ai_recycle_by_path_with_defer_removal_keeps_the_record_visible(window, tmp_path) -> None:
    source = tmp_path / "src"
    (a,) = make_jpegs(source, ["a.jpg"])
    open_folder(window, source, 1)

    ok = window._move_record_to_ai_recycle_by_path(a, defer_removal=True)

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
        moved = window._move_record_to_path(a, str(winners_dir), defer_removal=True)
        recycled = window._move_record_to_ai_recycle_by_path(b, defer_removal=True)
        assert calls == [], "no rebuild yet while removal is deferred"
        removed = window._remove_records_by_paths([a, b])

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

    moved = window._move_record_to_path(a, str(winners_dir), defer_removal=True, batch_id=batch_id)
    recycled = window._move_record_to_ai_recycle_by_path(b, defer_removal=True, batch_id=batch_id)
    window._remove_records_by_paths([a, b])

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
