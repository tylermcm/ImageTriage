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
from image_triage.preview_controller import PreviewController
from image_triage.scanner import normalized_path_key
from image_triage.ui.native_image_layer import CAMERA_RAW_FIRST_KEY


@pytest.fixture
def controller():
    app = QApplication.instance() or QApplication([])
    parent = QObject()
    ctl = PreviewController(parent)
    yield ctl
    ctl._photocraft_save_timer.stop()
    ctl._photocraft_dwell_timer.stop()
    ctl._photocraft_executor.shutdown(wait=True)
    ctl._photocraft_preview_executor.shutdown(wait=True)
    app.processEvents()


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


def test_the_setting_decides_the_launch_flag(controller):
    from image_triage.app_identity import user_settings

    settings = user_settings()
    settings.setValue(CAMERA_RAW_FIRST_KEY, False)
    assert controller._photocraft_launch_args() == ()
    settings.setValue(CAMERA_RAW_FIRST_KEY, True)
    assert controller._photocraft_launch_args() == ("--raw-workspace",)
    settings.setValue(CAMERA_RAW_FIRST_KEY, False)


def test_camera_raw_is_left_before_the_revision_is_read_and_the_photo_saved(controller, tmp_path):
    proc = process(tmp_path)
    controller._save_photocraft_sidecar_if_dirty(proc)
    names = [c[0] for c in proc.control.method_calls]
    assert proc.control.method_calls[0][1] == ("ui.cameraRaw", {"done": True})
    assert names.index("document_revision") > 0
    assert "app.stash" in methods(proc.control)


def test_autosave_never_closes_a_camera_raw_dialog_being_worked_in(controller, tmp_path):
    proc = process(tmp_path)
    proc.control.call.return_value = {"open": True}
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    proc.control.call.assert_called_once_with("ui.cameraRaw")
    proc.control.document_revision.assert_not_called()


def test_autosave_saves_once_the_dialog_is_closed(controller, tmp_path):
    proc = process(tmp_path)
    proc.control.call.side_effect = [{"open": False}, {"job": 5, "pending": True}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    assert methods(proc.control) == ["ui.cameraRaw", "app.stash"]


def test_an_editor_without_the_workspace_is_handled(controller, tmp_path):
    proc = process(tmp_path)
    proc.control.call.side_effect = [bridge.PhotoCraftError("unknown method `ui.cameraRaw`"), {"job": 5, "pending": True}]
    controller._save_photocraft_sidecar_if_dirty(proc)
    assert proc.camera_raw_first is False
    assert "app.stash" in methods(proc.control)


def test_other_errors_from_camera_raw_are_not_swallowed(controller, tmp_path):
    proc = process(tmp_path)
    proc.control.call.side_effect = bridge.PhotoCraftError("camera raw exploded")
    with pytest.raises(bridge.PhotoCraftError):
        controller._save_photocraft_sidecar_if_dirty(proc)


def test_without_the_workspace_nothing_about_camera_raw_is_asked(controller, tmp_path):
    proc = process(tmp_path, camera_raw_first=False)
    controller._save_photocraft_sidecar_if_dirty(proc)
    assert "ui.cameraRaw" not in methods(proc.control)


def test_a_closed_document_goes_back_to_camera_raw(controller, tmp_path):
    proc = process(tmp_path)
    proc.control.document_revision.return_value = None
    closed = []
    controller._photocraft_document_closed.connect(closed.append)
    controller._save_photocraft_sidecar_if_dirty(proc)
    assert closed == [str(tmp_path / "frame.jpg")]
    assert proc.current_source is None and proc.current_sidecar is None


def test_a_closed_document_is_only_reopened_in_camera_raw_mode(controller, tmp_path):
    proc = process(tmp_path, camera_raw_first=False)
    proc.control.document_revision.return_value = None
    closed = []
    controller._photocraft_document_closed.connect(closed.append)
    controller._save_photocraft_sidecar_if_dirty(proc)
    assert closed == []


def test_the_reopen_requests_the_same_photo_at_once(controller, tmp_path, monkeypatch):
    photo = str(tmp_path / "frame.jpg")
    controller._photocraft = process(tmp_path)
    preview = SimpleNamespace(
        isVisible=lambda: True, show_browsing_picture=Mock(), set_photocraft_loading=Mock(),
        native_first_active=Mock(return_value=True),
    )
    controller._window = SimpleNamespace(_preview=preview)
    raws = []
    monkeypatch.setattr(controller, "_load_photocraft", lambda generation, path: raws.append(path))
    controller._photocraft_current_path = photo
    controller._reopen_closed_document(photo)
    controller._photocraft_executor.submit(lambda: None).result()
    preview.show_browsing_picture.assert_called_once()
    assert raws == [photo]


def test_the_reopen_is_dropped_if_the_user_has_moved_on(controller, tmp_path):
    preview = SimpleNamespace(isVisible=lambda: True, show_browsing_picture=Mock())
    controller._window = SimpleNamespace(_preview=preview)
    controller._photocraft_current_path = str(tmp_path / "other.jpg")
    controller._reopen_closed_document(str(tmp_path / "frame.jpg"))
    preview.show_browsing_picture.assert_not_called()


def test_the_browsing_picture_waits_for_camera_raw_to_lay_out(controller, tmp_path, monkeypatch):
    proc = process(tmp_path)
    views = iter([{"cameraRaw": None}, {"cameraRaw": None}, {"cameraRaw": {"open": True, "view": {"x": 1, "y": 2, "width": 3, "height": 4}}}])
    proc.control.call.side_effect = lambda *a, **k: next(views)
    monkeypatch.setattr(controller, "CAMERA_RAW_POLL_S", 0.0)
    controller._photocraft_generation = 4
    seen, up = [], []
    controller._photocraft_viewport_ready.connect(lambda g, v: seen.append(v))
    controller._photocraft_camera_raw_up.connect(lambda g, p: up.append(p))
    controller._refresh_photocraft_viewport(proc, 4, "frame.jpg")
    assert seen and seen[0]["cameraRaw"]["view"]["width"] == 3
    assert up == ["frame.jpg"]


def test_the_wait_for_camera_raw_is_bounded(controller, tmp_path, monkeypatch):
    proc = process(tmp_path)
    proc.control.call.return_value = {"cameraRaw": None}
    monkeypatch.setattr(controller, "CAMERA_RAW_POLL_S", 0.0)
    monkeypatch.setattr(controller, "CAMERA_RAW_WAIT_S", 0.05)
    controller._photocraft_generation = 4
    up = []
    controller._photocraft_camera_raw_up.connect(lambda g, p: up.append(p))
    controller._refresh_photocraft_viewport(proc, 4, "frame.jpg")
    assert up == ["frame.jpg"], "a document Camera Raw could not open on must still release the picture"


def test_a_stale_wait_gives_up_when_the_user_moves_on(controller, tmp_path, monkeypatch):
    proc = process(tmp_path)
    proc.control.call.return_value = {"cameraRaw": None}
    monkeypatch.setattr(controller, "CAMERA_RAW_POLL_S", 0.0)
    controller._photocraft_generation = 5
    up = []
    controller._photocraft_camera_raw_up.connect(lambda g, p: up.append(p))
    controller._refresh_photocraft_viewport(proc, 4, "frame.jpg")
    assert up == []


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
