"""Characterization of winner-copy syncing and the winner toggle on the real MainWindow.

WI-4.1c moved the actual file-copy/link work for a winner/reject toggle onto
the background annotation-persistence worker (see image_triage/annotation_queue.py),
so a toggle's copy no longer exists synchronously when `_toggle_winner` returns.
The pure sync-logic tests below exercise `file_ops.sync_winner_copy_for_paths`
directly (the exact function the worker calls); the MainWindow tests flush the
persistence queue with `flush_blocking()` before asserting on the result.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from image_triage.file_ops import sync_winner_copy_for_paths
from image_triage.models import WinnerMode
from tests.harness import make_jpegs, open_folder


def _flush(window) -> None:
    window._annotation_persistence_queue.flush_blocking()


def test_copy_mode_makes_a_real_copy_and_leaves_the_source(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    sync_winner_copy_for_paths((src,), True, str(tmp_path), WinnerMode.COPY)

    copy = tmp_path / "_winners" / "a.jpg"
    assert copy.read_bytes() == Path(src).read_bytes()
    assert not os.path.samefile(src, copy)
    assert os.path.exists(src)


def test_unmarking_removes_the_copy_but_keeps_the_source(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    sync_winner_copy_for_paths((src,), True, str(tmp_path), WinnerMode.COPY)
    sync_winner_copy_for_paths((src,), False, str(tmp_path), WinnerMode.COPY)

    assert not (tmp_path / "_winners" / "a.jpg").exists()
    assert os.path.exists(src)


def test_hardlink_mode_links_to_the_same_file(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    sync_winner_copy_for_paths((src,), True, str(tmp_path), WinnerMode.HARDLINK)

    assert os.path.samefile(src, tmp_path / "_winners" / "a.jpg")
    sync_winner_copy_for_paths((src,), False, str(tmp_path), WinnerMode.HARDLINK)
    assert os.path.exists(src) and not (tmp_path / "_winners" / "a.jpg").exists()


def test_logical_mode_touches_no_files(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    sync_winner_copy_for_paths((src,), True, str(tmp_path), WinnerMode.LOGICAL)

    assert not (tmp_path / "_winners").exists()


def test_existing_winner_file_is_never_overwritten_on_mark(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    winners = tmp_path / "_winners"
    winners.mkdir()
    (winners / "a.jpg").write_bytes(b"a file that was already here")
    sync_winner_copy_for_paths((src,), True, str(tmp_path), WinnerMode.COPY)

    assert (winners / "a.jpg").read_bytes() == b"a file that was already here"


def test_unmark_never_deletes_a_same_named_file_the_app_did_not_create(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    winners = tmp_path / "_winners"
    winners.mkdir()
    (winners / "a.jpg").write_bytes(b"a different photo that shares the name")

    kept = sync_winner_copy_for_paths((src,), False, str(tmp_path), WinnerMode.COPY)

    assert (winners / "a.jpg").read_bytes() == b"a different photo that shares the name"
    assert kept == ("a.jpg",)


def test_unmark_keeps_a_copy_whose_content_size_no_longer_matches_the_source(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    sync_winner_copy_for_paths((src,), True, str(tmp_path), WinnerMode.COPY)
    (tmp_path / "_winners" / "a.jpg").write_bytes(b"edited elsewhere")

    kept = sync_winner_copy_for_paths((src,), False, str(tmp_path), WinnerMode.COPY)

    assert kept == ("a.jpg",) and (tmp_path / "_winners" / "a.jpg").exists()


def test_unmark_keeps_the_file_when_the_source_is_gone(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    sync_winner_copy_for_paths((src,), True, str(tmp_path), WinnerMode.COPY)
    os.remove(src)

    kept = sync_winner_copy_for_paths((src,), False, str(tmp_path), WinnerMode.COPY)

    assert kept == ("a.jpg",) and (tmp_path / "_winners" / "a.jpg").exists()


def test_unmark_removes_an_untouched_copy_and_reports_nothing_kept(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    sync_winner_copy_for_paths((src,), True, str(tmp_path), WinnerMode.COPY)

    kept = sync_winner_copy_for_paths((src,), False, str(tmp_path), WinnerMode.COPY)

    assert kept == () and not (tmp_path / "_winners" / "a.jpg").exists()


def test_unmark_removes_a_symlink_that_points_at_the_source(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    winners = tmp_path / "_winners"
    winners.mkdir()
    try:
        os.symlink(src, winners / "a.jpg")
    except OSError:
        pytest.skip("symlinks need privileges on this machine")

    kept = sync_winner_copy_for_paths((src,), False, str(tmp_path), WinnerMode.COPY)

    assert kept == () and not os.path.lexists(winners / "a.jpg") and os.path.exists(src)


def test_a_failure_part_way_removes_the_copies_already_made(tmp_path) -> None:
    first, second = make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    real = None
    from image_triage import file_ops

    real = file_ops.create_winner_artifact
    calls = []

    def flaky(source, destination, mode):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError("disk full")
        real(source, destination, mode)

    with pytest.raises(OSError, match="disk full"):
        sync_winner_copy_for_paths(
            (first, second), True, str(tmp_path), WinnerMode.COPY, create_artifact=flaky
        )

    assert not (tmp_path / "_winners" / "a.jpg").exists()


def test_syncing_from_inside_the_winners_folder_does_nothing(tmp_path) -> None:
    winners = tmp_path / "_winners"
    (src,) = make_jpegs(winners, ["a.jpg"])
    sync_winner_copy_for_paths((src,), True, str(winners), WinnerMode.COPY)

    assert not (winners / "_winners").exists()


def test_toggle_winner_off_tells_the_user_when_it_leaves_a_foreign_file(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    main_window._winner_mode = WinnerMode.COPY
    open_folder(main_window, tmp_path, 1)
    main_window._toggle_winner(0, advance_override=False)
    _flush(main_window)
    (tmp_path / "_winners" / "a.jpg").write_bytes(b"replaced by the user")

    main_window._toggle_winner(0, advance_override=False)
    _flush(main_window)

    assert (tmp_path / "_winners" / "a.jpg").read_bytes() == b"replaced by the user"
    assert "not a copy Image Triage made" in main_window.statusBar().currentMessage()


def test_toggle_winner_flips_state_copies_and_records_undo(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    main_window._winner_mode = WinnerMode.COPY
    open_folder(main_window, tmp_path, 2)
    record = main_window._records[0]

    main_window._toggle_winner(0, advance_override=False)
    _flush(main_window)

    annotation = main_window._annotations[record.path]
    assert annotation.winner and not annotation.reject
    assert (tmp_path / "_winners" / Path(record.path).name).exists()
    assert main_window._undo_stack[-1].kind == "annotation"
    assert main_window._undo_stack[-1].original_winner is False

    main_window._toggle_winner(0, advance_override=False)
    _flush(main_window)
    assert not main_window._annotations[record.path].winner
    assert not (tmp_path / "_winners" / Path(record.path).name).exists()


def test_marking_winner_clears_reject(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    main_window._winner_mode = WinnerMode.LOGICAL
    open_folder(main_window, tmp_path, 1)
    record = main_window._records[0]

    main_window._toggle_reject(0, advance_override=False)
    assert main_window._annotations[record.path].reject
    main_window._toggle_winner(0, advance_override=False)
    _flush(main_window)

    annotation = main_window._annotations[record.path]
    assert annotation.winner and not annotation.reject


def test_toggle_winner_failure_reverts_the_annotation_via_status_bar(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    main_window._winner_mode = WinnerMode.COPY
    open_folder(main_window, tmp_path, 1)
    record = main_window._records[0]

    with patch(
        "image_triage.annotation_queue.sync_winner_copy_for_paths",
        side_effect=OSError("boom"),
    ):
        main_window._toggle_winner(0, advance_override=False)
        _flush(main_window)

    annotation = main_window._annotations.get(record.path)
    assert annotation is None or not annotation.winner
    assert "Reverted winner state" in main_window.statusBar().currentMessage()
    # The optimistic toggle still recorded an undo entry; the async failure
    # only reverts the in-memory annotation, matching the existing
    # persistence-failure rollback convention.
    assert main_window._undo_stack


def test_undoing_a_winner_toggle_removes_the_copy(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    main_window._winner_mode = WinnerMode.COPY
    open_folder(main_window, tmp_path, 1)
    record = main_window._records[0]

    main_window._toggle_winner(0, advance_override=False)
    _flush(main_window)
    assert (tmp_path / "_winners" / Path(record.path).name).exists()
    main_window._undo_last_action()
    _flush(main_window)

    assert not (tmp_path / "_winners" / Path(record.path).name).exists()
    annotation = main_window._annotations.get(record.path)
    assert annotation is None or not annotation.winner
