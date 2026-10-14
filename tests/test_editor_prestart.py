"""The editor is started quietly in the background after Image Triage boots: no photo open, below-normal priority, rooted at the
folder being browsed, and used (not restarted) by the first photo that is opened in it."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage import preview_controller as controller_module
from image_triage.preview_controller import PreviewController


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(qapp, monkeypatch):
    # The settings this controller reads: on, unless a test says otherwise.
    values = {}
    monkeypatch.setattr(controller_module, "user_settings", lambda *a, **k: SimpleNamespace(value=lambda key, default=None, type=None: values.get(key, default)))
    monkeypatch.delenv("IMAGE_TRIAGE_NO_EDITOR_PRESTART", raising=False)
    parent = QObject()
    ctl = PreviewController(parent)
    ctl.settings_values = values
    ctl._photocraft_executable = "photocraft.exe"
    yield ctl
    ctl._photocraft_save_timer.stop()
    ctl._photocraft_dwell_timer.stop()
    ctl._photocraft_executor.shutdown(wait=True)
    ctl._photocraft_preview_executor.shutdown(wait=True)
    qapp.processEvents()


def prestarted_proc(tmp_path, **kwargs):
    control = Mock()
    control.document_revision.return_value = None
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=123, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits", executable="photocraft.exe", idle_priority=True, **kwargs,
    )
    proc.process.poll.return_value = None
    return proc


def drain(ctl):
    ctl._photocraft_executor.submit(lambda: None).result()


def test_boot_starts_the_editor_with_no_photo_at_low_priority_in_the_folder_being_browsed(controller, tmp_path, monkeypatch):
    launch = Mock(return_value=prestarted_proc(tmp_path))
    monkeypatch.setattr(bridge, "launch_photocraft", launch)
    controller._window = SimpleNamespace(_current_folder=str(tmp_path))
    controller.run_deferred_editor_start()
    drain(controller)
    (initial,), kwargs = launch.call_args
    assert initial is None, "no photo is opened"
    assert kwargs["idle"] is True
    assert kwargs["read_root"] == str(tmp_path)
    assert (tmp_path / ".image_triage_edits").is_dir(), "the editor needs its write folder to exist"
    assert controller._photocraft is launch.return_value
    controller.run_deferred_editor_start()  # already running: not started twice
    drain(controller)
    assert launch.call_count == 1


@pytest.mark.parametrize("why", ["setting off", "env switch", "no editor installed", "no folder", "already running"])
def test_it_is_not_started_when_it_should_not_be(controller, tmp_path, monkeypatch, why):
    launch = Mock(return_value=prestarted_proc(tmp_path))
    monkeypatch.setattr(bridge, "launch_photocraft", launch)
    controller._window = SimpleNamespace(_current_folder=str(tmp_path))
    if why == "setting off":
        controller.settings_values[controller_module.EDITOR_PRESTART_KEY] = False
    elif why == "env switch":
        monkeypatch.setenv("IMAGE_TRIAGE_NO_EDITOR_PRESTART", "1")
    elif why == "no editor installed":
        controller._photocraft_executable = None
    elif why == "no folder":
        controller._window = SimpleNamespace(_current_folder="")
    else:
        controller._photocraft = prestarted_proc(tmp_path)
    controller.run_deferred_editor_start()
    drain(controller)
    launch.assert_not_called()


def test_a_folder_that_is_gone_is_left_alone_and_nothing_is_created(controller, tmp_path, monkeypatch):
    launch = Mock()
    monkeypatch.setattr(bridge, "launch_photocraft", launch)
    gone = tmp_path / "unplugged card"
    controller._window = SimpleNamespace(_current_folder=str(gone))
    controller.run_deferred_editor_start()
    drain(controller)
    launch.assert_not_called()
    assert not gone.exists()


def test_a_failed_start_is_logged_and_leaves_the_controller_ready_to_start_one_later(controller, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(bridge, "launch_photocraft", Mock(side_effect=bridge.PhotoCraftError("no")))
    controller._window = SimpleNamespace(_current_folder=str(tmp_path))
    controller.run_deferred_editor_start()
    drain(controller)
    assert controller._photocraft is None
    assert "Could not start the editor in the background" in caplog.text


def test_the_first_photo_uses_the_running_editor_at_normal_priority_and_puts_it_in_the_window(controller, tmp_path, monkeypatch):
    photo = tmp_path / "IMG.NEF"
    photo.write_bytes(b"x")
    proc = prestarted_proc(tmp_path)
    controller._photocraft = proc
    launch = Mock()
    monkeypatch.setattr(bridge, "launch_photocraft", launch)
    raised = Mock()
    monkeypatch.setattr(bridge, "raise_priority", raised)
    monkeypatch.setattr(controller, "_resolve_photocraft_target", lambda path: (path, str(tmp_path / ".image_triage_edits" / "IMG.NEF.pcraft")))
    switched = Mock(return_value=True)
    monkeypatch.setattr(controller, "_switch_photocraft_document", switched)
    monkeypatch.setattr(controller, "_sync_photocraft_theme", Mock())
    windows = []
    controller._photocraft_window_ready.connect(lambda generation, p: windows.append(p))
    controller._open_in_photocraft(None, str(photo))
    launch.assert_not_called()
    raised.assert_called_once_with(proc)
    assert windows == [proc], "the window is embedded before the photo renders, as for a freshly launched editor"
    switched.assert_called_once()


def test_a_photo_in_another_folder_starts_a_fresh_editor_instead(controller, tmp_path, monkeypatch):
    other = tmp_path.parent / (tmp_path.name + "_other")
    other.mkdir()
    photo = other / "IMG.NEF"
    photo.write_bytes(b"x")
    old = prestarted_proc(tmp_path)
    controller._photocraft = old
    fresh = prestarted_proc(other)
    fresh.idle_priority = False
    launch = Mock(return_value=fresh)
    monkeypatch.setattr(bridge, "launch_photocraft", launch)
    monkeypatch.setattr(controller, "_resolve_photocraft_target", lambda path: (path, str(other / ".image_triage_edits" / "IMG.NEF.pcraft")))
    monkeypatch.setattr(controller, "_sync_photocraft_theme", Mock())
    monkeypatch.setattr(controller, "_save_photocraft_sidecar_if_dirty", Mock())
    monkeypatch.setattr(controller, "_wait_photocraft_stashes", Mock())
    old.process.poll.side_effect = [None, None, None] + [0] * 20  # running when it is checked, gone once it was asked to quit
    controller._open_in_photocraft(None, str(photo))
    old.control.quit.assert_called_once()
    assert launch.call_args.args[0] == str(photo), "the new folder's editor is started on its photo"
    assert launch.call_args.kwargs.get("idle", False) is False
    assert controller._photocraft is fresh


# --- the bridge ----------------------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="priority classes and window handling are Windows-only")
def test_an_idle_launch_opens_nothing_and_starts_below_normal(tmp_path, monkeypatch):
    started = {}

    class FakeProcess:
        pid = 4242

        def __init__(self, args, **kwargs):
            started["args"], started["flags"] = args, kwargs.get("creationflags", 0)

        def poll(self):
            return None

    control = Mock()
    monkeypatch.setattr(bridge.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(bridge, "_connect_with_retry", lambda port, token, process: control)
    monkeypatch.setattr(bridge, "_find_window_for_pid", lambda pid: 99)
    monkeypatch.setattr(bridge.ctypes.windll.user32, "ShowWindow", Mock(), raising=False)
    proc = bridge._launch_photocraft_binary(
        None, read_root=str(tmp_path), write_root=str(tmp_path), executable="photocraft.exe", extra_args=("--raw-workspace",), idle=True,
    )
    assert started["flags"] & bridge._BELOW_NORMAL_PRIORITY_CLASS, "started at below-normal priority"
    control.app_open.assert_not_called()
    assert proc.idle_priority and proc.camera_raw_first and proc.current_source is None
    proc.token_file.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "nt", reason="priority classes are Windows-only")
def test_raising_the_priority_of_a_background_editor_sets_the_process_to_normal(tmp_path):
    psutil = pytest.importorskip("psutil")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], creationflags=bridge._BELOW_NORMAL_PRIORITY_CLASS)
    try:
        proc = bridge.PhotoCraftProcess(process=child, control=Mock(), hwnd=1, token_file=tmp_path / "t", read_root=tmp_path, idle_priority=True)
        assert psutil.Process(child.pid).nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS
        bridge.raise_priority(proc)
        assert psutil.Process(child.pid).nice() == psutil.NORMAL_PRIORITY_CLASS
        assert not proc.idle_priority
        bridge.raise_priority(proc)  # again: nothing to do
    finally:
        child.kill()
        child.wait()


# --- shutting down while the background start is still running ------------------------------------------------------

def test_shutting_down_during_the_background_start_ends_the_editor_it_was_starting(controller, tmp_path, monkeypatch):
    """The start is slow (a minute on a cold machine is possible); shutdown must not return while it is still running and then
    have it publish an editor nobody will shut down."""
    import threading

    started, release = threading.Event(), threading.Event()
    proc = prestarted_proc(tmp_path)
    ended = []
    monkeypatch.setattr(bridge.PhotoCraftProcess, "shutdown", lambda self, **kwargs: ended.append(self))

    def slow_launch(*args, **kwargs):
        started.set()
        release.wait(5)
        return proc

    monkeypatch.setattr(bridge, "launch_photocraft", slow_launch)
    controller._window = SimpleNamespace(_current_folder=str(tmp_path))
    controller.run_deferred_editor_start()
    assert started.wait(5)
    threading.Timer(0.2, release.set).start()
    controller.shutdown_photocraft()
    assert controller._photocraft is None, "the late editor is never taken into use"
    assert ended == [proc], "and it is shut down, not left running"


def test_a_background_start_still_queued_at_shutdown_never_runs(controller, tmp_path, monkeypatch):
    import threading

    gate = threading.Event()
    controller._photocraft_executor.submit(gate.wait, 5)  # something else is on the editor's worker
    launch = Mock(return_value=prestarted_proc(tmp_path))
    monkeypatch.setattr(bridge, "launch_photocraft", launch)
    controller._window = SimpleNamespace(_current_folder=str(tmp_path))
    controller.run_deferred_editor_start()
    controller.shutdown_photocraft()
    gate.set()
    drain(controller)
    launch.assert_not_called()
    assert controller._photocraft is None


def test_no_background_start_begins_after_shutdown(controller, tmp_path, monkeypatch):
    launch = Mock(return_value=prestarted_proc(tmp_path))
    monkeypatch.setattr(bridge, "launch_photocraft", launch)
    controller._window = SimpleNamespace(_current_folder=str(tmp_path))
    controller.shutdown_photocraft()
    controller.run_deferred_editor_start()
    drain(controller)
    launch.assert_not_called()


def test_shutdown_with_nothing_started_returns_at_once(controller):
    import time

    started = time.perf_counter()
    controller.shutdown_photocraft()
    assert time.perf_counter() - started < 0.5
