"""The editor in a window of its own (Linux and macOS, where another program's window cannot be put inside ours): Image Triage's window
steps aside while the editor is up, and closing the editor's window saves the photo and brings Image Triage back."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from image_triage import photocraft_bridge as bridge
from image_triage.qml_shell.editor_host import EDITING, IDLE, EditorHost, SeparateWindow, WindowMover
from test_qml_shell import qapp  # noqa: F401  (the application fixture)
from test_qml_shell_editor import Proc, folder, wait  # noqa: F401  (the same fakes)


class Separate:
    """A `SeparateWindow` that records what it was asked, and says the window was closed once `close` is set."""

    supported = True
    separate = True

    def __init__(self):
        self.calls, self.close = [], False

    def show(self, proc, visible):
        self.calls.append(("show", visible))

    def close_requested(self, proc):
        closed, self.close = self.close, False
        return closed

    def place(self, *args):                      # never called for a window of its own
        self.calls.append(("place", *args))

    def attach(self, *args):
        self.calls.append(("attach", *args))


class ShellWindow:
    def __init__(self):
        self.visible, self.calls = True, []

    def winId(self):
        return 5

    def hide(self):
        self.visible = False
        self.calls.append("hide")

    def show(self):
        self.visible = True
        self.calls.append("show")

    def isVisible(self):
        return self.visible

    def raise_(self):
        self.calls.append("raise")

    def requestActivate(self):
        self.calls.append("activate")


def make(qapp, folder, dirty=True):
    def launch(initial, *, read_root, write_root, idle=False, **_):
        return Proc(read_root, dirty)

    mover, window = Separate(), ShellWindow()
    host = EditorHost(launcher=launch, mover=mover, detector=lambda: True, parent=qapp)
    host.setWindow(window)
    return host, mover, window


def editing(qapp, folder, dirty=True):
    host, mover, window = make(qapp, folder, dirty)
    host.setGeometry(0, 44, 1440, 856)
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    return host, mover, window


def test_the_editor_is_shown_and_our_window_steps_aside(qapp, folder):
    host, mover, window = editing(qapp, folder)
    assert mover.calls == [("show", True)], "nothing is attached or placed: the window manager places a window of its own"
    assert not window.visible
    host.setGeometry(0, 44, 1000, 600)
    assert mover.calls == [("show", True)]
    host.shutdown()


def test_closing_the_editors_window_saves_the_photo_and_brings_our_window_back(qapp, folder):
    host, mover, window = editing(qapp, folder)
    proc = host._proc
    closed = []
    host.closed.connect(closed.append)
    mover.close = True                                                       # the user closed the editor's window
    assert wait(qapp, lambda: host.state == IDLE)
    assert proc.control.saved == 1 and closed == [str(folder / "a.jpg")]
    assert window.visible and window.calls[-3:] == ["show", "raise", "activate"]
    assert mover.calls[-1] == ("show", False) and not proc.stopped           # kept for the next photo, as the embedded editor is
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    assert host._proc is proc and mover.calls[-1] == ("show", True) and not window.visible
    host.shutdown()


def test_a_failed_save_on_close_is_said_in_our_window_and_keep_editing_goes_back_to_the_editor(qapp, folder):
    host, mover, window = editing(qapp, folder)
    failures = []
    host.closeFailed.connect(failures.append)

    def broken(proc):
        raise RuntimeError("the share went away")

    host._settle = broken
    mover.close = True
    assert wait(qapp, lambda: bool(failures))
    assert host.state == EDITING and window.visible, "the card that says so is in our window"
    host.setPresentationBlocked(True)                                        # the card opens ...
    host.setPresentationBlocked(False)                                       # ... and Keep editing closes it
    assert mover.calls[-1] == ("show", True) and not window.visible
    host.shutdown()


def test_an_editor_that_exits_brings_our_window_back(qapp, folder):
    host, mover, window = editing(qapp, folder)
    closed = []
    host.closed.connect(closed.append)
    host._proc.stopped = True
    assert wait(qapp, lambda: host.state == IDLE)
    assert closed == [str(folder / "a.jpg")] and window.visible
    host.shutdown()


def test_the_default_window_follows_the_platform():
    host = EditorHost(detector=lambda: True)
    try:
        assert isinstance(host._mover, WindowMover if bridge.EMBEDDED_WINDOW else SeparateWindow)
    finally:
        host.shutdown()


# ---- starting the editor -----------------------------------------------------------------------------------------------------------


def _start(tmp_path, monkeypatch, control):
    seen = {}

    class FakeProcess:
        pid = 4242

        def __init__(self, args, **kwargs):
            seen["args"] = args

        def poll(self):
            return None

    monkeypatch.setattr(bridge, "EMBEDDED_WINDOW", False)
    monkeypatch.setattr(bridge.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(bridge, "_connect_with_retry", lambda port, token, process: control)
    monkeypatch.setattr(bridge, "_find_window_for_pid", Mock(side_effect=AssertionError("there is no window to find")))
    monkeypatch.setattr(bridge, "_end_process", lambda process: None)
    proc = bridge._launch_photocraft_binary(None, read_root=str(tmp_path), write_root=str(tmp_path), executable="photocraft", idle=True)
    return proc, seen


def test_off_windows_the_editor_is_started_in_a_window_of_its_own(tmp_path, monkeypatch):
    control = Mock(host_window_supported=True)
    proc, seen = _start(tmp_path, monkeypatch, control)
    try:
        assert seen["args"][1:3] == ["--hosted", "--hosted-window"]
        assert proc.hwnd == 0
    finally:
        proc.token_file.unlink(missing_ok=True)


def test_an_editor_without_a_window_of_its_own_is_refused(tmp_path, monkeypatch):
    control = Mock(host_window_supported=False)
    with pytest.raises(bridge.PhotoCraftCompatibilityError, match="window of its own"):
        _start(tmp_path, monkeypatch, control)


def test_the_capability_and_the_control_call():
    control = bridge.PhotoCraftControl.__new__(bridge.PhotoCraftControl)
    sent = []
    control.call = lambda method, params=None: sent.append((method, params)) or {"visible": True, "closeRequested": False}
    assert control.host_window(True) == {"visible": True, "closeRequested": False}
    control.host_window()
    control.host_window(False)
    assert sent == [("ui.hostWindow", {"visible": True}), ("ui.hostWindow", {}), ("ui.hostWindow", {"visible": False})]


def test_the_handoff_says_whether_the_editor_can_have_a_window_of_its_own():
    from test_lean_autosave import handoff_control

    with_it = handoff_control({"version": 2, "hostWindow": 1})
    with_it.require_hosted_handoff()
    assert with_it.host_window_supported is True
    without = handoff_control({"version": 2})
    without.require_hosted_handoff()
    assert without.host_window_supported is False


def test_an_editor_whose_window_cannot_hide_is_ended_once_its_photo_is_saved(qapp, folder):
    # Wayland without Xwayland: the editor stays on screen when closed, so it is saved and then ended rather than kept.
    host, mover, window = editing(qapp, folder)
    mover.can_hide = lambda proc: False
    proc = host._proc
    mover.close = True
    assert wait(qapp, lambda: host.state == IDLE)
    assert proc.control.saved == 1 and window.visible
    assert host._proc is None and wait(qapp, lambda: proc.stopped)
    host.openPath(str(folder / "a.jpg"))                                     # the next photo starts a new editor
    assert wait(qapp, lambda: host.state == EDITING)
    assert host._proc is not proc
    host.shutdown()


def test_the_window_reports_whether_it_can_hide():
    proc = Mock()
    proc.fast_lane.return_value.host_window.return_value = {"visible": True, "closeRequested": True, "canHide": False}
    assert SeparateWindow.close_requested(proc) is True and SeparateWindow.can_hide(proc) is False
    proc.fast_lane.return_value.host_window.return_value = {"visible": True, "closeRequested": False}    # an editor that does not say
    assert SeparateWindow.close_requested(proc) is False and SeparateWindow.can_hide(proc) is True


def test_the_real_editor_process_can_be_told_whether_its_window_hides():
    # PhotoCraftProcess has slots: what the window reports must be one of its fields, or noting it fails (as it once did).
    import dataclasses

    proc = bridge.PhotoCraftProcess(process=Mock(), control=Mock(), hwnd=0, token_file=bridge.Path("token"), read_root=bridge.Path("."))
    assert proc.window_can_hide is True
    SeparateWindow._note(proc, {"canHide": False})
    assert SeparateWindow.can_hide(proc) is False
    assert "window_can_hide" in {field.name for field in dataclasses.fields(bridge.PhotoCraftProcess)}
