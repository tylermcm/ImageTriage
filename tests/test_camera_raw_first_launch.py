"""Camera Raw first: the editor is launched in its workspace when the setting is on, Camera Raw's changes
are committed before a save or a switch (and never closed by autosave), the browsing picture stays until
Camera Raw is up, and closing the full editor's document returns to the photo in Camera Raw."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.scanner import normalized_path_key




def process(tmp_path, *, camera_raw_first=True, revision=2):
    source = str(tmp_path / "frame.jpg")
    control = Mock()
    control.document_revision.return_value = revision
    control.call.return_value = {"job": 5, "pending": True}
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=123, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits",
        source_path=source, current_source=normalized_path_key(source),
        current_sidecar=bridge.sidecar_pcraft_path(source), opened_revision=1,
        camera_raw_first=camera_raw_first,
    )
    proc.process.poll.return_value = None
    return proc


def methods(control):
    return [call.args[0] for call in control.call.call_args_list]


































def test_the_launch_passes_the_flag_to_the_binary(tmp_path, monkeypatch):
    captured = {}

    class Boom(Exception):
        pass

    def popen(args, **kwargs):
        captured["args"] = args
        raise Boom

    monkeypatch.setattr(bridge.subprocess, "Popen", popen)
    with pytest.raises(Boom):
        bridge._launch_photocraft_binary(
            str(tmp_path / "a.jpg"), read_root=str(tmp_path), write_root=str(tmp_path), executable="photocraft.exe",
            extra_args=("--raw-workspace",),
        )
    assert "--raw-workspace" in captured["args"] and captured["args"][1] == "--hosted"
