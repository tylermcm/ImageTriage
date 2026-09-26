"""Characterization of winner-copy syncing and the winner toggle on the real MainWindow.

Several of these pin behaviour that WI-1.2 intends to change (marked HAZARD);
they exist so that change is a deliberate, visible diff.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from image_triage.models import WinnerMode
from tests.harness import make_jpegs, open_folder


def _sync(window, paths, enabled, folder, mode):
    window._winner_mode = mode
    window._sync_winner_copy_for_paths(tuple(paths), enabled, str(folder))


def test_copy_mode_makes_a_real_copy_and_leaves_the_source(main_window, tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    _sync(main_window, [src], True, tmp_path, WinnerMode.COPY)

    copy = tmp_path / "_winners" / "a.jpg"
    assert copy.read_bytes() == Path(src).read_bytes()
    assert not os.path.samefile(src, copy)
    assert os.path.exists(src)


def test_unmarking_removes_the_copy_but_keeps_the_source(main_window, tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    _sync(main_window, [src], True, tmp_path, WinnerMode.COPY)
    _sync(main_window, [src], False, tmp_path, WinnerMode.COPY)

    assert not (tmp_path / "_winners" / "a.jpg").exists()
    assert os.path.exists(src)


def test_hardlink_mode_links_to_the_same_file(main_window, tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    _sync(main_window, [src], True, tmp_path, WinnerMode.HARDLINK)

    assert os.path.samefile(src, tmp_path / "_winners" / "a.jpg")
    _sync(main_window, [src], False, tmp_path, WinnerMode.HARDLINK)
    assert os.path.exists(src) and not (tmp_path / "_winners" / "a.jpg").exists()


def test_logical_mode_touches_no_files(main_window, tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    _sync(main_window, [src], True, tmp_path, WinnerMode.LOGICAL)

    assert not (tmp_path / "_winners").exists()


def test_existing_winner_file_is_never_overwritten_on_mark(main_window, tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    winners = tmp_path / "_winners"
    winners.mkdir()
    (winners / "a.jpg").write_bytes(b"a file that was already here")
    _sync(main_window, [src], True, tmp_path, WinnerMode.COPY)

    assert (winners / "a.jpg").read_bytes() == b"a file that was already here"


def test_unmark_never_deletes_a_same_named_file_the_app_did_not_create(main_window, tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    winners = tmp_path / "_winners"
    winners.mkdir()
    (winners / "a.jpg").write_bytes(b"a different photo that shares the name")
    main_window._winner_mode = WinnerMode.COPY

    kept = main_window._sync_winner_copy_for_paths((src,), False, str(tmp_path))

    assert (winners / "a.jpg").read_bytes() == b"a different photo that shares the name"
    assert kept == ("a.jpg",)


def test_unmark_keeps_a_copy_whose_content_size_no_longer_matches_the_source(main_window, tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    _sync(main_window, [src], True, tmp_path, WinnerMode.COPY)
    (tmp_path / "_winners" / "a.jpg").write_bytes(b"edited elsewhere")

    kept = main_window._sync_winner_copy_for_paths((src,), False, str(tmp_path))

    assert kept == ("a.jpg",) and (tmp_path / "_winners" / "a.jpg").exists()


def test_unmark_keeps_the_file_when_the_source_is_gone(main_window, tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    _sync(main_window, [src], True, tmp_path, WinnerMode.COPY)
    os.remove(src)

    kept = main_window._sync_winner_copy_for_paths((src,), False, str(tmp_path))

    assert kept == ("a.jpg",) and (tmp_path / "_winners" / "a.jpg").exists()


def test_unmark_removes_an_untouched_copy_and_reports_nothing_kept(main_window, tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    _sync(main_window, [src], True, tmp_path, WinnerMode.COPY)

    kept = main_window._sync_winner_copy_for_paths((src,), False, str(tmp_path))

    assert kept == () and not (tmp_path / "_winners" / "a.jpg").exists()


def test_unmark_removes_a_symlink_that_points_at_the_source(main_window, tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    winners = tmp_path / "_winners"
    winners.mkdir()
    try:
        os.symlink(src, winners / "a.jpg")
    except OSError:
        pytest.skip("symlinks need privileges on this machine")

    kept = main_window._sync_winner_copy_for_paths((src,), False, str(tmp_path))

    assert kept == () and not os.path.lexists(winners / "a.jpg") and os.path.exists(src)


def test_toggle_winner_off_tells_the_user_when_it_leaves_a_foreign_file(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    main_window._winner_mode = WinnerMode.COPY
    open_folder(main_window, tmp_path, 1)
    main_window._toggle_winner(0, advance_override=False)
    (tmp_path / "_winners" / "a.jpg").write_bytes(b"replaced by the user")

    main_window._toggle_winner(0, advance_override=False)

    assert (tmp_path / "_winners" / "a.jpg").read_bytes() == b"replaced by the user"
    assert "not a copy Image Triage made" in main_window.statusBar().currentMessage()


def test_a_failure_part_way_removes_the_copies_already_made(main_window, tmp_path) -> None:
    first, second = make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    real = main_window._create_winner_artifact
    calls = []

    def flaky(source, destination, mode):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError("disk full")
        real(source, destination, mode)

    main_window._winner_mode = WinnerMode.COPY
    with patch.object(main_window, "_create_winner_artifact", side_effect=flaky):
        with pytest.raises(OSError, match="disk full"):
            main_window._sync_winner_copy_for_paths((first, second), True, str(tmp_path))

    assert not (tmp_path / "_winners" / "a.jpg").exists()


def test_syncing_from_inside_the_winners_folder_does_nothing(main_window, tmp_path) -> None:
    winners = tmp_path / "_winners"
    (src,) = make_jpegs(winners, ["a.jpg"])
    _sync(main_window, [src], True, winners, WinnerMode.COPY)

    assert not (winners / "_winners").exists()


def test_toggle_winner_flips_state_copies_and_records_undo(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    main_window._winner_mode = WinnerMode.COPY
    open_folder(main_window, tmp_path, 2)
    record = main_window._records[0]

    main_window._toggle_winner(0, advance_override=False)

    annotation = main_window._annotations[record.path]
    assert annotation.winner and not annotation.reject
    assert (tmp_path / "_winners" / Path(record.path).name).exists()
    assert main_window._undo_stack[-1].kind == "annotation"
    assert main_window._undo_stack[-1].original_winner is False

    main_window._toggle_winner(0, advance_override=False)
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

    annotation = main_window._annotations[record.path]
    assert annotation.winner and not annotation.reject


def test_toggle_winner_failure_rolls_back_the_annotation_and_warns(main_window, dialogs, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    main_window._winner_mode = WinnerMode.COPY
    open_folder(main_window, tmp_path, 1)
    record = main_window._records[0]

    with patch.object(main_window, "_sync_winner_copy", side_effect=OSError("boom")):
        main_window._toggle_winner(0, advance_override=False)

    assert not main_window._annotations[record.path].winner
    assert not main_window._undo_stack
    assert any(kind == "warning" and title == "Winner Sync Failed" for kind, title, _ in dialogs.messages)


def test_undoing_a_winner_toggle_removes_the_copy(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    main_window._winner_mode = WinnerMode.COPY
    open_folder(main_window, tmp_path, 1)
    record = main_window._records[0]

    main_window._toggle_winner(0, advance_override=False)
    assert (tmp_path / "_winners" / Path(record.path).name).exists()
    main_window._undo_last_action()

    assert not (tmp_path / "_winners" / Path(record.path).name).exists()
    annotation = main_window._annotations.get(record.path)
    assert annotation is None or not annotation.winner
