from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject, QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.cache import edit_state_for
from image_triage.edit_render_headless import render_edited_image
from image_triage.edit_storage import session_has_edits
from image_triage.models import ImageRecord




def process(tmp_path, *, revision=2):
    source = str(tmp_path / "frame.jpg")
    control = Mock()
    control.document_revision.return_value = revision
    control.call.return_value = {"job": 5, "pending": True}
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=123, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits",
        source_path=source, current_source=source,
        current_sidecar=bridge.sidecar_pcraft_path(source), opened_revision=1,
    )
    proc.process.poll.return_value = None
    return proc


def test_sidecars_do_not_collide_for_raw_jpeg_pair(tmp_path):
    assert bridge.sidecar_pcraft_path(str(tmp_path / "frame.nef")) != bridge.sidecar_pcraft_path(str(tmp_path / "frame.jpg"))




def test_read_and_write_roots_are_independent(tmp_path):
    proc = process(tmp_path)
    assert proc.path_within_root(proc.source_path) == "frame.jpg"
    assert proc.path_within_write_root(proc.source_path) is None
    assert proc.path_within_write_root(str(proc.current_sidecar)) == "frame.jpg.pcraft"






















@pytest.mark.parametrize("session, expected", [
    ({"active": None, "documents": []}, None),
    ({"active": 1, "documents": [{"index": 0, "revision": 3}, {"index": 1, "revision": 8}]}, 8),
])
def test_revision_query_handles_empty_editor(session, expected):
    control = object.__new__(bridge.PhotoCraftControl)
    control.execute = Mock(return_value=session)
    assert control.document_revision() == expected
    control.execute.assert_called_once_with("session.inspect")
















def test_saved_render_feeds_thumbnails_and_headless_exports(tmp_path):
    source = str(tmp_path / "photo.jpg")
    project = bridge.sidecar_pcraft_path(source)
    project.parent.mkdir()
    project.write_bytes(b"project")
    image = QImage(12, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    image.save(str(bridge.rendered_preview_path(source)))
    assert session_has_edits(source)
    assert edit_state_for(source) != (0, 0)
    rendered = render_edited_image(source, target_size=QSize(6, 6))
    assert rendered is not None and rendered.pixelColor(0, 0).red() == 255




def test_spawn_failure_removes_token_file(tmp_path, monkeypatch):
    token = tmp_path / "token.txt"
    import os
    monkeypatch.setattr(bridge.tempfile, "mkstemp", lambda **kwargs: (os.open(token, os.O_CREAT | os.O_WRONLY), str(token)))
    monkeypatch.setattr(bridge.subprocess, "Popen", Mock(side_effect=OSError("cannot start")))
    with pytest.raises(OSError):
        bridge.launch_photocraft("photo.jpg", read_root=str(tmp_path), write_root=str(tmp_path), executable="missing.exe")
    assert not token.exists()


def test_shutdown_waits_after_force_kill(tmp_path):
    import subprocess
    proc = process(tmp_path)
    proc.process.wait.side_effect = [subprocess.TimeoutExpired("photocraft", 1), subprocess.TimeoutExpired("photocraft", 1), 0]
    proc.shutdown(timeout=1)
    proc.process.kill.assert_called_once()
    assert proc.process.wait.call_count == 3
    proc.control.close.assert_called_once()














def test_stale_override_recovers_with_companion_binary(tmp_path, monkeypatch):
    proc = process(tmp_path)
    launch = Mock(side_effect=[bridge.PhotoCraftCompatibilityError("unknown app.stash"), proc])
    monkeypatch.setattr(bridge, "_launch_photocraft_binary", launch)
    monkeypatch.setattr(bridge, "companion_photocraft_executables", lambda: ["updated-host.exe"])
    result = bridge.launch_photocraft("photo.jpg", read_root=str(tmp_path), write_root=str(tmp_path), executable="old-release.exe")
    assert result is proc
    assert proc.executable == "updated-host.exe"
    assert [call.kwargs["executable"] for call in launch.call_args_list] == ["old-release.exe", "updated-host.exe"]


def test_file_errors_do_not_retry_another_editor(tmp_path, monkeypatch):
    launch = Mock(side_effect=bridge.PhotoCraftError("invalid image"))
    monkeypatch.setattr(bridge, "_launch_photocraft_binary", launch)
    monkeypatch.setattr(bridge, "companion_photocraft_executables", lambda: ["updated-host.exe"])
    with pytest.raises(bridge.PhotoCraftError, match="invalid image"):
        bridge.launch_photocraft("photo.jpg", read_root=str(tmp_path), write_root=str(tmp_path), executable="old-release.exe")
    launch.assert_called_once()






def test_same_checkout_override_selects_host_without_launching_old_release(tmp_path, monkeypatch):
    from image_triage import shell_actions
    checkout = tmp_path / "photocraft"
    hosted = checkout / "target" / "debug" / "photocraft-host.exe"
    ordinary = checkout / "target" / "release" / "photocraft.exe"
    hosted.parent.mkdir(parents=True)
    ordinary.parent.mkdir(parents=True)
    hosted.touch()
    ordinary.touch()
    import os
    os.utime(ordinary, (1_000_000, 1_000_000))  # the older build must lose to the newer one
    (checkout / "Cargo.toml").touch()
    monkeypatch.setattr(shell_actions, "__file__", str(tmp_path / "ImageTriage" / "image_triage" / "shell_actions.py"))
    monkeypatch.setattr(shell_actions.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("IMAGE_TRIAGE_PHOTOCRAFT_EXE", str(ordinary))
    detector = bridge.detect_photocraft_executable
    detector.cache_clear()
    try:
        assert detector() == str(hosted)
    finally:
        detector.cache_clear()
