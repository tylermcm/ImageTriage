"""Two-stage display: a cached preview opens in PhotoCraft at once, the RAW follows once the
selection rests, and a RAW load for a photo already left can never surface or bind."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage import photocraft_preview




def fast_proc(tmp_path):
    control = Mock()
    control.document_revision.return_value = 2
    control.call.return_value = {"job": 5, "pending": True}
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=123, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits",
    )
    proc.process.poll.return_value = None
    proc.fast = Mock()
    proc.fast.call.return_value = {"jobs": []}
    return proc


def test_preview_file_is_made_once_and_reused(tmp_path, monkeypatch):
    from image_triage import imaging

    photo = tmp_path / "frame.NEF"
    photo.write_bytes(b"raw")
    loads = []

    def fake_load(path, size, *, prefer_embedded):
        loads.append((path, size.width(), prefer_embedded))
        image = QImage(40, 30, QImage.Format.Format_RGB32)
        image.fill(QColor("green"))
        return image, None

    monkeypatch.setattr(imaging, "load_image_for_display", fake_load)
    root = tmp_path / ".image_triage_edits"
    first = photocraft_preview.ensure_preview(str(photo), root)
    again = photocraft_preview.ensure_preview(str(photo), root)
    assert first == again and first.is_file() and first.parent == root / "previews"
    assert len(loads) == 1 and loads[0][2] is True
    assert not list(first.parent.glob("*.tmp"))
    photo.write_bytes(b"changed raw")
    assert photocraft_preview.preview_path_for(str(photo), root) != first


def test_preview_failure_names_the_photo(tmp_path, monkeypatch):
    from image_triage import imaging

    monkeypatch.setattr(imaging, "load_image_for_display", lambda *a, **k: (QImage(), "no embedded preview"))
    photo = tmp_path / "frame.NEF"
    photo.write_bytes(b"raw")
    with pytest.raises(RuntimeError, match="frame.NEF"):
        photocraft_preview.ensure_preview(str(photo), tmp_path / ".image_triage_edits")
























def test_cancel_open_jobs_never_touches_saves():
    fast = Mock()
    fast.call.side_effect = lambda method, params=None: (
        {"jobs": [{"id": 1, "command": "app.stash", "state": "running"}, {"id": 2, "command": "app.open", "state": "done"}]}
        if method == "jobs.list"
        else {}
    )
    proc = bridge.PhotoCraftProcess(process=Mock(), control=Mock(), hwnd=1, token_file=None, read_root=None)
    proc.fast = fast
    assert proc.cancel_open_jobs() == 0
    assert not [c for c in fast.call.call_args_list if c.args[0] == "jobs.cancel"]


def test_an_open_is_abandoned_if_the_selection_moved_on_before_it_was_sent():
    control = bridge.PhotoCraftControl.__new__(bridge.PhotoCraftControl)
    control.call = Mock(return_value={})
    with pytest.raises(bridge.PhotoCraftSuperseded):
        control.app_open("camera.NEF", replace=True, should_continue=lambda: False)
    control.call.assert_not_called()
    control.app_open("camera.NEF", replace=True, should_continue=lambda: True)
    assert control.call.call_args.args[0] == "app.open"
