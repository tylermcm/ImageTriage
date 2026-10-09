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
from image_triage.preview import FullScreenPreview, PreviewEntry
from image_triage.preview_controller import PreviewController


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
    # Keep the parent and app alive until all worker signals have drained.
    app.processEvents()


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


def test_raw_handoff_preserves_source_and_requires_capability(controller, tmp_path):
    path = str(tmp_path / "frame.dng")
    target, sidecar = controller._resolve_photocraft_target(path)
    assert target == path
    assert sidecar == str(bridge.sidecar_pcraft_path(path))
    control = object.__new__(bridge.PhotoCraftControl)
    control.call = Mock(return_value={})
    with pytest.raises(bridge.PhotoCraftCompatibilityError):
        control.app_open("frame.dng", replace=True)
    control.raw_smart_supported = True
    control.app_open("frame.dng", replace=True)
    control.call.assert_called_once_with("app.open", {"path": "frame.dng", "replace": True, "rawSmartObject": True})


def test_raw_dialog_commits_before_revision_and_stash(controller, tmp_path):
    proc = process(tmp_path)
    proc.control.raw_smart_supported = True
    controller._save_photocraft_sidecar_if_dirty(proc)
    calls = proc.control.method_calls
    assert calls[0][0:2] == ("call", ("ui.rawDevelopment", {"commit": True}))
    assert calls[1][0] == "document_revision"
    assert calls[2][1][0] == "app.stash"


def test_autosave_does_not_commit_or_close_raw_dialog(controller, tmp_path):
    proc = process(tmp_path)
    proc.control.raw_smart_supported = True
    proc.control.call.return_value = {"open": True}
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    proc.control.call.assert_called_once_with("ui.rawDevelopment")
    proc.control.document_revision.assert_not_called()


def test_read_and_write_roots_are_independent(tmp_path):
    proc = process(tmp_path)
    assert proc.path_within_root(proc.source_path) == "frame.jpg"
    assert proc.path_within_write_root(proc.source_path) is None
    assert proc.path_within_write_root(str(proc.current_sidecar)) == "frame.jpg.pcraft"


def test_switch_stashes_then_replaces_without_closing(controller, tmp_path):
    proc = process(tmp_path)
    controller._switch_photocraft_document(proc, str(tmp_path / "next.jpg"), str(bridge.sidecar_pcraft_path(str(tmp_path / "next.jpg"))))
    calls = proc.control.method_calls
    names = [call[0] for call in calls]
    assert names.index("call") < names.index("app_open")
    proc.control.close_document.assert_not_called()
    proc.control.app_open.assert_called_once_with("next.jpg", replace=True)
    assert proc.opened_revision == 1
    assert calls[names.index("call")][1][0] == "app.stash"
    assert proc.pending_stashes


def test_failed_stash_request_does_not_close_document(controller, tmp_path):
    proc = process(tmp_path)
    proc.control.call.side_effect = bridge.PhotoCraftError("disk full")
    with pytest.raises(bridge.PhotoCraftError):
        controller._switch_photocraft_document(proc, str(tmp_path / "next.jpg"), str(bridge.sidecar_pcraft_path(str(tmp_path / "next.jpg"))))
    proc.control.close_document.assert_not_called()
    assert proc.current_sidecar == bridge.sidecar_pcraft_path(proc.source_path)


def test_nested_folder_gets_a_process_with_its_own_write_root(controller, tmp_path, monkeypatch):
    old = process(tmp_path, revision=1)
    nested = tmp_path / "nested"
    nested.mkdir()
    new = process(nested, revision=1)
    controller._photocraft = old
    launch = Mock(return_value=new)
    monkeypatch.setattr(bridge, "launch_photocraft", launch)
    controller._open_in_photocraft(None, str(nested / "next.jpg"))
    old.control.quit.assert_called_once()
    assert controller._photocraft is new
    assert launch.call_args.kwargs["read_root"] == str(nested)
    assert launch.call_args.kwargs["write_root"] == str(nested / ".image_triage_edits")


def test_existing_json_edits_become_the_first_photocraft_source(controller, tmp_path, monkeypatch):
    from image_triage import edit_storage, edit_render_headless
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor("green"))
    monkeypatch.setattr(edit_storage, "session_has_edits", lambda path: True)
    monkeypatch.setattr(edit_render_headless, "render_edited_image", lambda path: image)
    source, target = controller._resolve_photocraft_target(str(tmp_path / "old.jpg"))
    assert source.endswith("old.jpg__photocraft_legacy.tiff")
    assert Path(source).is_file()
    assert target.endswith("old.jpg.pcraft")


def test_revision_inspection_failure_does_not_discard_edits(controller, tmp_path):
    proc = process(tmp_path)
    proc.control.document_revision.side_effect = bridge.PhotoCraftError("channel closed")
    with pytest.raises(bridge.PhotoCraftError):
        controller._save_photocraft_sidecar_if_dirty(proc)
    proc.control.close_document.assert_not_called()


def test_clean_document_is_not_saved(controller, tmp_path):
    proc = process(tmp_path, revision=1)
    controller._save_photocraft_sidecar_if_dirty(proc)
    proc.control.call.assert_not_called()


@pytest.mark.parametrize("same_photo", [True, False])
def test_selection_reopens_after_document_tab_closed(controller, tmp_path, same_photo):
    from image_triage.scanner import normalized_path_key

    proc = process(tmp_path)
    proc.current_source = normalized_path_key(proc.source_path)
    proc.control.document_revision.return_value = None
    controller._photocraft = proc
    target = proc.source_path if same_photo else str(tmp_path / "next.jpg")
    controller._open_in_photocraft(None, target)
    assert controller._photocraft is proc
    proc.control.app_open.assert_called_once_with(Path(target).name, replace=True)
    assert proc.current_source == normalized_path_key(target)
    assert proc.current_sidecar == bridge.sidecar_pcraft_path(target)
    assert not any(call.args[0] == "app.stash" for call in proc.control.call.call_args_list)
    proc.control.quit.assert_not_called()


def test_same_open_photo_keeps_unsaved_document(controller, tmp_path):
    from image_triage.scanner import normalized_path_key

    proc = process(tmp_path)
    proc.current_source = normalized_path_key(proc.source_path)
    controller._photocraft = proc
    controller._open_in_photocraft(None, proc.source_path)
    proc.control.app_open.assert_not_called()
    proc.control.call.assert_not_called()


def test_closed_document_clears_binding_without_losing_pending_save(controller, tmp_path):
    proc = process(tmp_path)
    proc.control.document_revision.return_value = None
    key = str(proc.current_sidecar)
    proc.pending_stashes[key] = (5, proc.source_path)
    controller._save_photocraft_sidecar_if_dirty(proc)
    assert proc.current_source is None
    assert proc.current_sidecar is None
    assert proc.pending_stashes[key] == (5, proc.source_path)
    proc.control.call.assert_not_called()


@pytest.mark.parametrize("failed", [False, True])
def test_reopen_closed_document_uses_pending_or_failed_snapshot(controller, tmp_path, failed):
    proc = process(tmp_path)
    proc.control.document_revision.return_value = None
    key = str(proc.current_sidecar)
    if failed:
        proc.stash_errors[key] = "disk full"
    else:
        proc.pending_stashes[key] = (5, proc.source_path)
    controller._photocraft = proc
    controller._open_in_photocraft(None, proc.source_path)
    proc.control.app_open.assert_called_once_with("frame.jpg.pcraft", replace=True)


@pytest.mark.parametrize("session, expected", [
    ({"active": None, "documents": []}, None),
    ({"active": 1, "documents": [{"index": 0, "revision": 3}, {"index": 1, "revision": 8}]}, 8),
])
def test_revision_query_handles_empty_editor(session, expected):
    control = object.__new__(bridge.PhotoCraftControl)
    control.execute = Mock(return_value=session)
    assert control.document_revision() == expected
    control.execute.assert_called_once_with("session.inspect")


def test_selected_filmstrip_thumbnail_requests_editor_again():
    preview = FullScreenPreview()
    preview.set_photocraft_available(True)
    record = ImageRecord("same.jpg", "same.jpg", 1, 1)
    preview._source_entries = [PreviewEntry(record, record.path)]
    preview._filmstrip_current = 0
    requested = []
    preview.photocraft_edit_requested.connect(requested.append)
    preview._handle_studio_filmstrip_selected(0)
    assert requested == [record.path]
    preview.close()


def test_filmstrip_theme_survives_refresh_resize_and_collapse():
    preview = FullScreenPreview()
    strip = preview._filmstrip
    colors = {"panel": "#fcfcfd", "dock": "#f0f0f3", "accent": "#6c5ce7", "text": "#18181c"}
    preview.set_photocraft_palette(colors)
    strip.set_source(4, 0)
    strip.set_mockup_metrics(1440, 900)
    strip.drag_resize(strip.MIN_THUMB_H + 20)
    assert strip._editor_palette == colors
    assert colors["panel"] in strip.styleSheet()
    assert strip._handle.height() == 6
    assert strip._footer is None
    assert preview._mockup_status_bar.isHidden()
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtTest import QTest
    handle = strip._handle
    def gap_color():
        return handle.grab().toImage().pixelColor(2, 2).name()
    handle.leaveEvent(QEvent(QEvent.Type.Leave))
    assert gap_color() == colors["dock"]
    handle.enterEvent(QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2)))
    assert gap_color() == colors["accent"]
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    handle.leaveEvent(QEvent(QEvent.Type.Leave))
    assert gap_color() == colors["accent"], "dragging lost the resize highlight"
    QTest.mouseMove(handle, QPoint(2, 12))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=QPoint(2, 12))
    assert gap_color() == colors["dock"]
    # Restore expansion if this offscreen mouse gesture was treated as a click.
    if strip.is_collapsed():
        strip.toggle_collapsed()
    from image_triage.ui.preview_studio import FilmstripThumb
    assert all(thumb._editor_palette == colors for thumb in strip._reel.findChildren(FilmstripThumb))
    strip.toggle_collapsed()
    assert strip.height() == 6
    strip.toggle_collapsed()
    assert strip.height() > 6
    preview.close()


def test_invalid_editor_palette_does_not_replace_current_theme():
    preview = FullScreenPreview()
    previous = preview._filmstrip._editor_palette
    preview.set_photocraft_palette({"panel": "invalid-css", "accent": "blue", "text": "white"})
    assert preview._filmstrip._editor_palette == previous
    preview.close()


def test_pending_project_can_be_reopened_before_disk_save(controller, tmp_path):
    proc = process(tmp_path)
    controller._photocraft = proc
    controller._save_photocraft_sidecar_if_dirty(proc)
    target, sidecar = controller._resolve_photocraft_target(proc.source_path)
    assert target == sidecar == str(proc.current_sidecar)
    assert not Path(target).exists()


def test_completed_save_notifies_main_app(controller, tmp_path):
    proc = process(tmp_path)
    controller._save_photocraft_sidecar_if_dirty(proc)
    saved = []
    controller._photocraft_saved.disconnect()
    controller._photocraft_saved.connect(saved.append)
    proc.control.call.return_value = {"jobs": [{"id": 5, "state": "done"}]}
    controller._poll_photocraft_stashes(proc)
    assert saved == [proc.source_path]
    assert not proc.pending_stashes


def test_failed_background_save_blocks_shutdown(controller, tmp_path):
    proc = process(tmp_path)
    controller._save_photocraft_sidecar_if_dirty(proc)
    controller._photocraft_save_failed.disconnect()
    proc.control.call.return_value = {"jobs": [{"id": 5, "state": "failed", "error": "disk full"}]}
    with pytest.raises(bridge.PhotoCraftError, match="disk full"):
        controller._wait_photocraft_stashes(proc)
    assert proc.stash_errors
    assert proc.opened_revision == -1


def test_navigation_does_not_wait_for_worker_and_skips_superseded_requests(controller):
    entered, release = Event(), Event()
    opens = []
    def load(_preview, path):
        opens.append(path)
        entered.set()
        release.wait(3)
    preview = SimpleNamespace(hide_photocraft_host=Mock(), set_photocraft_loading=Mock(), isVisible=lambda: False)
    controller._window = SimpleNamespace(_preview=preview)
    controller._open_in_photocraft = load
    controller.handle_photocraft_edit_requested("first.jpg")
    assert entered.wait(1)
    controller.handle_photocraft_edit_requested("skipped.jpg")
    controller.handle_photocraft_edit_requested("latest.jpg")
    preview.hide_photocraft_host.assert_not_called()
    release.set()
    controller._photocraft_future.result(timeout=3)
    assert opens == ["first.jpg", "latest.jpg"]


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


def test_preview_automatically_requests_editor_and_keeps_filmstrip(controller):
    preview = FullScreenPreview()
    preview.set_photocraft_available(True)
    requested = []
    preview.photocraft_edit_requested.connect(requested.append)
    record = ImageRecord(path="frame.jpg", name="frame.jpg", size=1, modified_ns=1)
    try:
        preview.show_entries([PreviewEntry(record, record.path)])
        assert requested == [record.path]
        assert preview.photocraft_button.isHidden()
        preview.show_photocraft_host()
        assert preview._photocraft_host.parentWidget() is preview.panes_widget
        preview.set_compare_mode(True)
        assert not preview.photocraft_edit_active()
    finally:
        preview.close()


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


def test_editor_process_exits_before_the_native_host_closes(controller, tmp_path):
    proc = process(tmp_path, revision=1)
    controller._photocraft = proc
    preview = FullScreenPreview()
    preview.photocraft_close_guard = controller.prepare_photocraft_preview_close
    closed = []
    preview.closed.connect(lambda: closed.append(controller._photocraft))
    preview.show()
    preview.close()
    assert closed == [None]
    proc.control.quit.assert_called_once()


def test_compatible_control_is_checked_without_opening_or_writing():
    control = object.__new__(bridge.PhotoCraftControl)
    control.call = Mock(side_effect=[
        {"version": 2},
        bridge.PhotoCraftError("stash requires path and preview"),
        bridge.PhotoCraftError("bind requires an open document, .pcraft path and .png preview"),
    ])
    control.require_hosted_handoff()
    assert control.call.call_args_list[1].args == ("app.stash", {})
    assert control.call.call_args_list[2].args == ("app.bind", {})


def test_incompatible_control_is_rejected_before_handoff():
    control = object.__new__(bridge.PhotoCraftControl)
    control.call = Mock(side_effect=bridge.PhotoCraftError("unknown method `app.stash`"))
    with pytest.raises(bridge.PhotoCraftError, match="lacks persistent hosted document replacement"):
        control.require_hosted_handoff()


def test_incompatible_launch_reaps_process_and_removes_token(tmp_path, monkeypatch):
    import os
    token = tmp_path / "token.txt"
    native = Mock()
    native.poll.return_value = None
    control = Mock()
    control.require_hosted_handoff.side_effect = bridge.PhotoCraftError("incompatible")
    monkeypatch.setattr(bridge.tempfile, "mkstemp", lambda **kwargs: (os.open(token, os.O_CREAT | os.O_WRONLY), str(token)))
    monkeypatch.setattr(bridge.subprocess, "Popen", Mock(return_value=native))
    monkeypatch.setattr(bridge, "_connect_with_retry", Mock(return_value=control))
    with pytest.raises(bridge.PhotoCraftError, match="incompatible"):
        bridge.launch_photocraft("photo.jpg", read_root=str(tmp_path), write_root=str(tmp_path), executable="old.exe")
    control.app_open.assert_not_called()
    control.close.assert_called_once()
    native.terminate.assert_called_once()
    native.wait.assert_called_once()
    assert not token.exists()


def test_companion_host_precedes_unrelated_path_install(tmp_path, monkeypatch):
    from image_triage import shell_actions
    checkout = tmp_path / "photocraft"
    hosted = checkout / "target" / "debug" / "photocraft-host.exe"
    hosted.parent.mkdir(parents=True)
    hosted.touch()
    (checkout / "Cargo.toml").touch()
    monkeypatch.setattr(shell_actions, "__file__", str(tmp_path / "ImageTriage" / "image_triage" / "shell_actions.py"))
    monkeypatch.delenv("IMAGE_TRIAGE_PHOTOCRAFT_EXE", raising=False)
    monkeypatch.setattr(shell_actions.shutil, "which", Mock(return_value="old-installed.exe"))
    # Call the original cached function, not the hermetic executable fixture.
    detector = bridge.detect_photocraft_executable
    detector.cache_clear()
    try:
        assert detector() == str(hosted)
    finally:
        detector.cache_clear()


def test_companion_unversioned_photocraft_exe_is_found(tmp_path, monkeypatch):
    from image_triage import shell_actions
    checkout = tmp_path / "photocraft"
    built = checkout / "target" / "debug" / "photocraft.exe"
    built.parent.mkdir(parents=True)
    built.touch()
    (checkout / "Cargo.toml").touch()
    monkeypatch.setattr(shell_actions, "__file__", str(tmp_path / "ImageTriage" / "image_triage" / "shell_actions.py"))
    monkeypatch.setattr(shell_actions.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert shell_actions.companion_photocraft_executables() == [str(built)]


def test_save_error_can_cancel_or_explicitly_discard(controller, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    proc = process(tmp_path)
    controller._window = SimpleNamespace(_preview=None)
    controller._photocraft = proc
    controller._save_active_photocraft = Mock(side_effect=bridge.PhotoCraftError("disk full"))
    warning = Mock(return_value=QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    assert not controller.prepare_photocraft_close()
    assert controller._photocraft is proc
    proc.control.quit.assert_not_called()
    warning.return_value = QMessageBox.StandardButton.Discard
    assert controller.prepare_photocraft_close()
    assert controller._photocraft is None
    proc.control.quit.assert_called_once()


def test_failed_initial_binding_cleans_up_editor(controller, tmp_path, monkeypatch):
    proc = process(tmp_path, revision=1)
    proc.control.call.side_effect = bridge.PhotoCraftError("unknown method app.bind")
    monkeypatch.setattr(bridge, "launch_photocraft", Mock(return_value=proc))
    with pytest.raises(bridge.PhotoCraftError, match="app.bind"):
        controller._open_in_photocraft(None, str(tmp_path / "frame.jpg"))
    assert controller._photocraft is None
    proc.control.quit.assert_called_once()


def test_failed_switch_keeps_previous_editor_accessible(controller, tmp_path, monkeypatch):
    proc = process(tmp_path)
    controller._photocraft = proc
    preview = SimpleNamespace(
        isVisible=lambda: True, _compare_mode=False, _collection_browse_mode=False,
        _before_after_enabled=False, photocraft_host_hwnd=lambda: 456,
        show_photocraft_host=Mock(), hide_photocraft_host=Mock(),
        set_photocraft_loading=Mock(),
    )
    status = Mock()
    controller._window = SimpleNamespace(_preview=preview, statusBar=lambda: status)
    monkeypatch.setattr(bridge, "embed_in_widget", Mock())
    controller._finish_photocraft_open(0, str(tmp_path / "next.jpg"), bridge.PhotoCraftError("disk full"))
    preview.show_photocraft_host.assert_called_once()
    preview.hide_photocraft_host.assert_not_called()
    assert "still showing frame.jpg" in status.showMessage.call_args.args[0]


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


def test_controller_remembers_the_compatible_fallback(controller, tmp_path, monkeypatch):
    proc = process(tmp_path, revision=1)
    proc.executable = "updated-host.exe"
    monkeypatch.setattr(bridge, "launch_photocraft", Mock(return_value=proc))
    controller._photocraft_executable = "old-release.exe"
    controller._open_in_photocraft(None, str(tmp_path / "frame.jpg"))
    assert controller._photocraft_executable == "updated-host.exe"


def test_native_window_is_attached_only_once(controller, tmp_path, monkeypatch):
    proc = process(tmp_path)
    preview = SimpleNamespace(photocraft_host_hwnd=lambda: 456, set_photocraft_loading=Mock())
    attach = Mock()
    monkeypatch.setattr(bridge, "embed_in_widget", attach)
    controller._attach_photocraft(preview, proc)
    controller._attach_photocraft(preview, proc)
    attach.assert_called_once_with(proc.hwnd, 456)


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


def test_editor_loading_covers_original_viewer_before_first_paint(controller, tmp_path, monkeypatch):
    preview = FullScreenPreview()
    preview.set_photocraft_available(True)
    record = ImageRecord(path=str(tmp_path / "frame.jpg"), name="frame.jpg", size=1, modified_ns=1)
    fullscreen = preview.showFullScreen
    def show():
        assert not preview._photocraft_loading_label.isHidden()
        assert preview._filmstrip.isHidden()
        fullscreen()
    monkeypatch.setattr(preview, "showFullScreen", show)
    try:
        preview.show_entries([PreviewEntry(record, record.path)])
        token = preview._load_token
        preview._request_preview_loads()
        assert preview._load_token == token, "the old viewer queued its own full-resolution decoder"
        assert preview._photocraft_loading_label.isVisible()
        assert preview.isFullScreen()
        assert preview._filmstrip.isHidden()
        preview.set_photocraft_loading(False)
        preview.show_photocraft_host()
        assert preview._photocraft_loading_label.isHidden()
        assert preview._filmstrip.isVisible()
        preview.set_photocraft_loading(True)
        assert preview._photocraft_loading_label.isHidden(), "switching hid the existing editor"
        assert preview._filmstrip.isVisible(), "switching hid the filmstrip"
        preview.set_compare_mode(True)
        assert preview._photocraft_loading_label.isHidden()
        assert not preview._uses_photocraft_editor()
    finally:
        preview.close()


def test_editor_failure_shows_error_cover_instead_of_original_viewer(controller):
    preview = FullScreenPreview()
    try:
        preview.show()
        preview.show_photocraft_error("Could not decode this file")
        assert preview._photocraft_loading_label.isVisible()
        assert "Could not decode" in preview._photocraft_loading_label.text()
    finally:
        preview.close()


def test_early_attachment_keeps_original_viewer_covered(controller, tmp_path, monkeypatch):
    proc = process(tmp_path)
    preview = FullScreenPreview()
    preview.set_photocraft_available(True)
    preview.show()
    preview.set_photocraft_loading(True)
    controller._window = SimpleNamespace(_preview=preview)
    attach, resize = Mock(), Mock()
    monkeypatch.setattr(bridge, "embed_in_widget", attach)
    monkeypatch.setattr(bridge, "resize_embedded", resize)
    try:
        controller._prepare_photocraft_window(0, proc)
        attach.assert_called_once()
        resize.assert_called_once()
        assert preview._photocraft_loading_label.isVisible()
        assert preview._photocraft_host.isHidden()
        assert proc.attached_parent == preview.photocraft_host_hwnd()
    finally:
        preview.close()


def test_nef_handoff_embeds_original_and_uses_scoped_sensor_adapter(tmp_path, monkeypatch):
    control = bridge.PhotoCraftControl.__new__(bridge.PhotoCraftControl)
    control.raw_smart_supported = True
    control.raw_sensor_supported = True
    control.read_root = tmp_path
    control.call = Mock(return_value={})
    from image_triage import photocraft_raw_source
    sensor = tmp_path / '.image_triage_edits' / 'sensor.dng'
    unpack = Mock(return_value=sensor)
    monkeypatch.setattr(photocraft_raw_source, 'materialize_sensor_dng', unpack)
    control.app_open('camera.NEF', replace=True)
    assert unpack.call_args.args[0] == str(tmp_path / 'camera.NEF')
    control.call.assert_called_once_with('app.open', {'path':'camera.NEF','replace':True,'rawSmartObject':True,'rawSensorPath':'.image_triage_edits/sensor.dng'})
    control.call.reset_mock()
    with pytest.raises(bridge.PhotoCraftError, match='sensor data'):
        control.app_open('../outside.NEF')
    control.call.assert_not_called()
