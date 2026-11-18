import os
import sys
import threading

import pytest

from image_triage.file_ops import FileMove, rename_paths


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to rename a file another program has open")
def test_a_rename_waits_for_a_file_that_is_open_for_a_moment(tmp_path):
    source = tmp_path / "a.jpg"
    source.write_bytes(b"photo")
    handle = open(source, "rb")              # a preview or thumbnail still reading it
    threading.Timer(0.3, handle.close).start()
    moved = rename_paths((FileMove(str(source), str(tmp_path / "wed_a.jpg")),))
    assert len(moved) == 1
    assert sorted(os.listdir(tmp_path)) == ["wed_a.jpg"]


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to rename a file another program has open")
def test_a_file_that_stays_open_still_fails_and_nothing_is_left_half_renamed(tmp_path, monkeypatch):
    import image_triage.file_ops as file_ops

    monkeypatch.setattr(file_ops, "_LOCK_WAIT_S", 0.2)
    first, second = tmp_path / "a.jpg", tmp_path / "b.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    with open(second, "rb"):
        with pytest.raises(PermissionError):
            rename_paths((FileMove(str(first), str(tmp_path / "x_a.jpg")), FileMove(str(second), str(tmp_path / "x_b.jpg"))))
    assert sorted(os.listdir(tmp_path)) == ["a.jpg", "b.jpg"]
