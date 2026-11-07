"""While a photo is up in Camera Raw the editor is asked to get the next raw ready, so opening it costs next to nothing."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.scanner import normalized_path_key




def process(tmp_path, *, prefetch=True, workspace=True):
    control = Mock()
    control.camera_raw_supported = True
    control.camera_raw_prefetch_supported = prefetch
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=1, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits", camera_raw_first=workspace,
    )
    proc.process.poll.return_value = None
    proc.fast = Mock()
    return proc


def folder(tmp_path, names):
    records = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"x" * 10)
        records.append(SimpleNamespace(path=str(path), is_folder=False))
    return records




def test_the_file_signature_is_size_and_modified_time(tmp_path):
    path = tmp_path / "a.NEF"
    path.write_bytes(b"12345")
    stat = path.stat()
    assert bridge.file_signature(str(path)) == f"5:{stat.st_mtime_ns}"
    assert bridge.file_signature(str(tmp_path / "missing.NEF")) is None




def test_the_bridge_sends_the_request_with_a_forward_slash_path():
    control = object.__new__(bridge.PhotoCraftControl)
    control.call = Mock(return_value={"queued": True})
    control.app_prefetch("sub\\next.NEF", "10:1")
    control.call.assert_called_once_with("app.prefetch", {"path": "sub/next.NEF", "signature": "10:1"})
