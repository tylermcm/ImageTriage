"""Characterization of move / delete / undo on the real MainWindow (WI-0.5)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from image_triage.models import DeleteMode, WinnerMode
from tests.harness import make_jpegs, open_folder


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
