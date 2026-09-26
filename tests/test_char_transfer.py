"""Characterization of TransferWorker (the engine behind Move To). Pins what it does today."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from image_triage.transfer_progress import TransferItem, TransferWorker


def _file(directory: Path, name: str, data: bytes = b"data") -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return str(path)


def _run(items, destination) -> TransferWorker:
    worker = TransferWorker(items, str(destination))
    worker.run()
    return worker


def test_same_volume_move_renames_and_reports_the_moves(tmp_path) -> None:
    src = _file(tmp_path / "a", "one.jpg", b"111")
    worker = _run([TransferItem(0, "one.jpg", (src,))], tmp_path / "dest")

    target = tmp_path / "dest" / "one.jpg"
    assert target.read_bytes() == b"111"
    assert not os.path.exists(src)
    (move,) = worker.result.moved[0]
    assert (move.source_path, move.target_path) == (src, str(target))
    assert worker.result.failed == {} and not worker.result.cancelled


def test_destination_folder_is_created(tmp_path) -> None:
    src = _file(tmp_path / "a", "one.jpg")
    _run([TransferItem(0, "one.jpg", (src,))], tmp_path / "new" / "nested")
    assert (tmp_path / "new" / "nested" / "one.jpg").exists()


def test_name_collision_gets_a_numeric_suffix_and_never_overwrites(tmp_path) -> None:
    existing = _file(tmp_path / "dest", "one.jpg", b"OLD")
    src = _file(tmp_path / "a", "one.jpg", b"NEW")
    worker = _run([TransferItem(0, "one.jpg", (src,))], tmp_path / "dest")

    assert Path(existing).read_bytes() == b"OLD"
    assert (tmp_path / "dest" / "one_1.jpg").read_bytes() == b"NEW"
    assert worker.result.moved[0][0].target_path == str(tmp_path / "dest" / "one_1.jpg")


def test_a_multi_file_item_moves_every_member_together(tmp_path) -> None:
    raw = _file(tmp_path / "a", "img.nef", b"raw")
    jpg = _file(tmp_path / "a", "img.jpg", b"jpg")
    worker = _run([TransferItem(0, "img", (raw, jpg))], tmp_path / "dest")

    assert sorted(p.name for p in (tmp_path / "dest").iterdir()) == ["img.jpg", "img.nef"]
    assert len(worker.result.moved[0]) == 2


def test_cross_volume_path_copies_then_removes_the_source(tmp_path) -> None:
    src = _file(tmp_path / "a", "big.jpg", b"x" * 1000)
    with patch("image_triage.transfer_progress.os.rename", side_effect=OSError("cross-device")):
        worker = _run([TransferItem(0, "big.jpg", (src,))], tmp_path / "dest")

    assert (tmp_path / "dest" / "big.jpg").read_bytes() == b"x" * 1000
    assert not os.path.exists(src)
    assert 0 in worker.result.moved


def test_failed_item_is_rolled_back_and_later_items_still_move(tmp_path) -> None:
    ok_first = _file(tmp_path / "a", "first.jpg", b"1")
    good = _file(tmp_path / "a", "good.jpg", b"g")
    gone = str(tmp_path / "a" / "missing.jpg")  # never existed
    items = [
        TransferItem(0, "pair", (ok_first, gone)),
        TransferItem(1, "good.jpg", (good,)),
    ]
    worker = _run(items, tmp_path / "dest")

    assert 0 in worker.result.failed and 0 not in worker.result.moved
    assert os.path.exists(ok_first), "the first file of the failed item must be restored"
    assert not (tmp_path / "dest" / "first.jpg").exists()
    assert (tmp_path / "dest" / "good.jpg").read_bytes() == b"g"
    assert 1 in worker.result.moved


def test_cancel_before_start_moves_nothing_and_flags_cancelled(tmp_path) -> None:
    src = _file(tmp_path / "a", "one.jpg")
    worker = TransferWorker([TransferItem(0, "one.jpg", (src,))], str(tmp_path / "dest"))
    worker.cancel()
    worker.run()

    assert os.path.exists(src)
    assert worker.result.cancelled and worker.result.moved == {}


def test_cancel_mid_item_rolls_back_files_already_moved(tmp_path) -> None:
    first = _file(tmp_path / "a", "first.jpg", b"1")
    second = _file(tmp_path / "a", "second.jpg", b"2")
    worker = TransferWorker([TransferItem(0, "pair", (first, second))], str(tmp_path / "dest"))
    original_move_file = worker._move_file

    def cancel_after_first(source, target, name):
        original_move_file(source, target, name)
        worker.cancel()

    with patch.object(worker, "_move_file", side_effect=cancel_after_first):
        worker.run()

    assert os.path.exists(first) and os.path.exists(second)
    assert not any((tmp_path / "dest").iterdir())
    assert worker.result.cancelled and worker.result.moved == {}


def test_unusable_destination_fails_every_item(tmp_path) -> None:
    src = _file(tmp_path / "a", "one.jpg")
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a folder")
    worker = _run([TransferItem(0, "one.jpg", (src,))], blocker / "sub")

    assert 0 in worker.result.failed
    assert os.path.exists(src)
