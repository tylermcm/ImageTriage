"""Recovery in the hosted editor: a photo switch that fails or is overtaken ends with the editor, the host and the selection
on the same photo; a failure that persists is announced once per photo and cause; and the editor's leftover save temporaries
are cleared when they are old. The editor is a stand-in (`test_qml_shell_editor.Proc`); the real one is not started."""
from __future__ import annotations

import os
import threading
import time

from image_triage import photocraft_bridge as bridge
from image_triage.qml_shell.editor_host import EDITING, IDLE, STARTING
from test_qml_shell import qapp  # noqa: F401  (the application fixture)
from test_qml_shell_editor import folder, make, wait  # noqa: F401


def editing(qapp, folder, name="a.jpg"):  # noqa: F811
    host, mover, launched = make(qapp, folder)
    host.openPath(str(folder / name))
    assert wait(qapp, lambda: host.state == EDITING)
    return host, host._proc, mover


def bound(control):
    """The sidecar name of every `app.bind` the editor was sent, in order."""
    return [params["path"] for method, params in control.calls if method == "app.bind"]


# --- (a) an abandoned switch ---------------------------------------------------------------------------------------------

def test_a_switch_that_fails_between_replacing_and_binding_puts_the_previous_photo_back(qapp, folder):  # noqa: F811
    host, proc, _mover = editing(qapp, folder)
    (folder / "b.jpg").write_bytes(b"x")
    control, real, failures, shown = proc.control, proc.control.call, [], []
    host.failed.connect(failures.append)
    host.opened.connect(shown.append)

    def call(method, params=None):
        if method == "app.bind" and params["path"].startswith("b.jpg"):
            raise bridge.PhotoCraftError("Synthetic bind failure")
        return real(method, params)

    control.call = call
    host.openPath(str(folder / "b.jpg"))
    assert wait(qapp, lambda: bool(failures) and host.state == EDITING)
    # The editor had replaced a.jpg with b.jpg; it was opened and bound again, so document, binding and host agree.
    assert [name for name, _replace in control.opened] == ["a.jpg", "b.jpg", "a.jpg"]
    assert bound(control)[-1].startswith("a.jpg") and proc.source_path == str(folder / "a.jpg")
    assert host.path == str(folder / "a.jpg") and shown == [] and not proc.stopped
    assert "Synthetic bind failure" in failures[0]


def test_a_switch_whose_open_gets_no_answer_is_not_assumed_to_have_left_the_document_alone(qapp, folder):  # noqa: F811
    host, proc, _mover = editing(qapp, folder)
    (folder / "b.jpg").write_bytes(b"x")
    control, real = proc.control, proc.control.app_open

    def app_open(path, replace=False, **kwargs):
        real(path, replace=replace, **kwargs)
        if path == "b.jpg":
            raise bridge.PhotoCraftError("PhotoCraft control 'app.open' failed: timed out") from TimeoutError("timed out")
        return {}

    control.app_open = app_open
    host.openPath(str(folder / "b.jpg"))
    assert wait(qapp, lambda: bool(host.error) and host.state == EDITING)
    assert [name for name, _replace in control.opened] == ["a.jpg", "b.jpg", "a.jpg"]
    assert host.path == proc.source_path == str(folder / "a.jpg") and bound(control)[-1].startswith("a.jpg")


def test_a_photo_the_editor_refuses_leaves_the_previous_one_without_reopening_it(qapp, folder):  # noqa: F811
    host, proc, _mover = editing(qapp, folder)
    (folder / "b.jpg").write_bytes(b"x")
    control, real = proc.control, proc.control.app_open

    def app_open(path, replace=False, **kwargs):
        if path == "b.jpg":
            raise bridge.PhotoCraftError("PhotoCraft control 'app.open' failed: unsupported file")
        return real(path, replace=replace, **kwargs)

    control.app_open = app_open
    host.openPath(str(folder / "b.jpg"))
    assert wait(qapp, lambda: bool(host.error) and host.state == EDITING)
    assert control.opened == [("a.jpg", True)] and host.path == proc.source_path == str(folder / "a.jpg")


def test_if_the_previous_photo_cannot_be_put_back_the_editor_is_stopped(qapp, folder):  # noqa: F811
    host, proc, _mover = editing(qapp, folder)
    (folder / "b.jpg").write_bytes(b"x")
    failures, closed = [], []
    host.failed.connect(failures.append)
    host.closed.connect(closed.append)

    real = proc.control.call

    def call(method, params=None):
        if method == "app.bind":
            raise bridge.PhotoCraftError("Synthetic bind failure")
        return real(method, params)

    proc.control.call = call
    host.openPath(str(folder / "b.jpg"))
    assert wait(qapp, lambda: bool(failures) and host.state == IDLE)
    # No editor is left that shows one photo while saves go to another's files.
    assert proc.stopped and host._proc is None and host.path == ""
    assert closed == [str(folder / "a.jpg")] and "previous photo" in failures[0]


def test_a_failure_after_the_new_photo_is_bound_ends_on_the_new_photo(qapp, folder):  # noqa: F811
    host, proc, _mover = editing(qapp, folder)
    (folder / "b.jpg").write_bytes(b"x")
    control, real, shown = proc.control, proc.control.call, []
    host.opened.connect(shown.append)

    def call(method, params=None):
        if method == "ui.cameraRaw" and params == {"open": True} and bound(control)[-1].startswith("b.jpg"):
            raise bridge.PhotoCraftError("Synthetic Camera Raw failure")
        return real(method, params)

    control.call = call
    host.openPath(str(folder / "b.jpg"))
    assert wait(qapp, lambda: bool(host.error) and host.state == EDITING)
    # The document and its binding are b.jpg, so that is what the host has open and what the selection is moved to.
    assert host.path == proc.source_path == str(folder / "b.jpg") and shown == [str(folder / "b.jpg")]
    control.call = real
    host.close()
    assert wait(qapp, lambda: host.state == IDLE)
    assert control.saved == 2                                 # a.jpg before the switch, then b.jpg under its own name


def test_a_request_made_while_a_switch_is_running_is_carried_out_afterwards(qapp, folder):  # noqa: F811
    host, proc, _mover = editing(qapp, folder)
    for name in ("b.jpg", "c.jpg"):
        (folder / name).write_bytes(b"x")
    control, real, release = proc.control, proc.control.app_open, threading.Event()

    def app_open(path, replace=False, **kwargs):
        if path == "b.jpg":
            assert release.wait(5)
        return real(path, replace=replace, **kwargs)

    control.app_open = app_open
    host.openPath(str(folder / "b.jpg"))
    assert host.state == STARTING
    host.openPath(str(folder / "c.jpg"))                      # the selection moved on while b.jpg was still opening
    release.set()
    assert wait(qapp, lambda: host.state == EDITING and host.path == str(folder / "c.jpg"))
    assert [name for name, _replace in control.opened] == ["a.jpg", "b.jpg", "c.jpg"]
    assert proc.source_path == str(folder / "c.jpg") and bound(control)[-1].startswith("c.jpg")


def test_an_overtaken_request_is_dropped_when_the_photo_being_left_cannot_be_saved(qapp, folder):  # noqa: F811
    host, proc, _mover = editing(qapp, folder)
    for name in ("b.jpg", "c.jpg"):
        (folder / name).write_bytes(b"x")
    release, failures = threading.Event(), []
    host.failed.connect(failures.append)

    def fail_save():
        assert release.wait(5)
        raise OSError("Synthetic save failure")

    proc.control.app_save = fail_save
    host.openPath(str(folder / "b.jpg"))
    host.openPath(str(folder / "c.jpg"))
    release.set()
    assert wait(qapp, lambda: bool(failures) and host.state == EDITING)
    for _ in range(20):
        qapp.processEvents()
    assert host.path == str(folder / "a.jpg") and proc.control.opened == [("a.jpg", True)] and len(failures) == 1


# --- (b) one notice per photo and cause ----------------------------------------------------------------------------------

def failing_save(proc, message):
    def fail_save():
        raise OSError(message)

    proc.control.app_save = fail_save


def test_a_failure_that_persists_is_announced_once_until_something_changes(qapp, folder):  # noqa: F811
    host, proc, _mover = editing(qapp, folder)
    for name in ("b.jpg", "c.jpg"):
        (folder / name).write_bytes(b"x")
    failures, errors = [], []
    host.failed.connect(failures.append)
    host.errorChanged.connect(lambda: errors.append(host.error))
    working = proc.control.app_save

    def switch(name):
        before = len(errors)
        host.openPath(str(folder / name))
        assert wait(qapp, lambda: host.state == EDITING and (len(errors) > before or host.path.endswith(name)))

    failing_save(proc, "automation write `a.jpg.pcraft` failed (NotFound) temp 23160-0")
    switch("b.jpg")
    failing_save(proc, "automation write `a.jpg.pcraft` failed (NotFound) temp 23160-1")   # the same failure, a new temp name
    switch("c.jpg")
    switch("b.jpg")
    assert len(failures) == 1 and "NotFound" in host.error    # said once; still readable where the status is shown

    failing_save(proc, "the disk is full")                    # a new cause is news
    switch("b.jpg")
    assert len(failures) == 2 and "disk is full" in failures[1]

    host.save()                                               # asked for by the person: always answered
    assert wait(qapp, lambda: len(failures) == 3)

    proc.control.app_save = working                           # it works again ...
    switch("b.jpg")
    assert host.path.endswith("b.jpg") and len(failures) == 3
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING and host.path.endswith("a.jpg"))
    failing_save(proc, "the disk is full")                    # ... so the same cause on the same photo is reported again
    switch("c.jpg")
    assert len(failures) == 4


# --- (c) leftover save temporaries ---------------------------------------------------------------------------------------

def aged(path, seconds):
    path.write_bytes(b"x")
    then = time.time() - seconds
    os.utime(path, (then, then))
    return path


def test_old_save_temporaries_are_removed_and_anything_a_running_save_could_own_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_process_exists", lambda pid: pid == 777)
    hour = bridge.STALE_SAVE_TEMP_S
    stale = aged(tmp_path / "._DSC1.NEF.cameraraw.json.23160-0.pcsave.tmp", hour + 60)
    unnamed = aged(tmp_path / "odd.pcsave.tmp", hour + 60)
    fresh = aged(tmp_path / "._DSC2.NEF.cameraraw.json.23160-1.pcsave.tmp", 30)                 # a save may be writing it now
    ours = aged(tmp_path / "._DSC3.jpg.pcraft.4242-7.pcsave.tmp", hour * 30)                    # our running editor's
    live = aged(tmp_path / "._DSC4.jpg.pcraft.777-0.pcsave.tmp", hour + 60)                     # some running process's number
    live_old = aged(tmp_path / "._DSC5.jpg.pcraft.777-1.pcsave.tmp", bridge.STALE_SAVE_TEMP_LIVE_PID_S + 60)
    other = aged(tmp_path / "_DSC1.NEF.cameraraw.json", hour * 30)
    other_tmp = aged(tmp_path / "notes.tmp", hour * 30)
    folder_named_like_one = tmp_path / "x.1-1.pcsave.tmp"
    folder_named_like_one.mkdir()
    assert bridge.sweep_stale_save_temps(tmp_path, live_pids=(4242,)) == 3
    assert not stale.exists() and not unnamed.exists() and not live_old.exists()
    assert all(path.exists() for path in (fresh, ours, live, other, other_tmp, folder_named_like_one))
    assert bridge.sweep_stale_save_temps(tmp_path / "missing") == 0


def test_the_edit_folder_is_swept_when_the_editor_starts_and_after_a_save(qapp, folder):  # noqa: F811
    edits = folder / ".edits"                                 # where the fixture puts the editor's write folder
    edits.mkdir()
    first = aged(edits / "._a.jpg.pcraft.1-0.pcsave.tmp", bridge.STALE_SAVE_TEMP_S * 30)
    host, proc, _mover = editing(qapp, folder)
    assert not first.exists()

    saved_root = bridge.sidecar_pcraft_path(str(folder / "a.jpg")).parent
    saved_root.mkdir(parents=True, exist_ok=True)
    second = aged(saved_root / "._a.jpg.pcraft.1-1.pcsave.tmp", bridge.STALE_SAVE_TEMP_S * 30)
    recent = aged(saved_root / "._a.jpg.pcraft.1-2.pcsave.tmp", 5)
    host.save()
    assert wait(qapp, lambda: not second.exists())
    assert recent.exists()
