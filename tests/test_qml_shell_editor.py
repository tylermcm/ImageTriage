"""The hosted PhotoCraft editor in the QML shell: starting it, opening a photo, saving on the way out, placing its window, failing visibly."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest

from image_triage.qml_shell.editor_host import EDITING, FAILED, IDLE, STARTING, EditorHost
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)


class Control:
    def __init__(self, dirty=True):
        self.opened, self.saved, self.dirty = [], 0, dirty
        self.calls, self.raw_specs = [], []

    def app_open(self, path, replace=False, **_):
        self.opened.append((path, replace))
        self.raw_specs.append(_.get("camera_raw"))
        return {}

    def call(self, method, params=None):
        self.calls.append((method, params))
        return {"open": bool(params and params.get("open")), "workspace": True}

    def app_save(self, path=None):
        self.saved += 1
        return {}

    def document_state(self):
        return (3, self.dirty)


class Proc:
    idle_priority = False
    attached_parent = None
    hwnd = 77

    def __init__(self, root, dirty=True):
        self.root, self.control, self.stopped = Path(root), Control(dirty), False

    def is_running(self):
        return not self.stopped

    def path_within_root(self, path):
        try:
            return Path(path).resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return None

    def shutdown(self, **_):
        self.stopped = True


class Mover:
    supported = True

    def __init__(self):
        self.calls = []

    def attach(self, hwnd, parent):
        self.calls.append(("attach", hwnd, parent))

    def place(self, hwnd, x, y, w, h):
        self.calls.append(("place", hwnd, x, y, w, h))

    def show(self, hwnd, visible):
        self.calls.append(("show", hwnd, visible))


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr("image_triage.edit_storage.ensure_edit_root", lambda directory: Path(directory) / ".edits")
    path = tmp_path / "shoot"
    path.mkdir()
    (path / "a.jpg").write_bytes(b"x")
    return path


def wait(qapp, condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        qapp.processEvents()
        time.sleep(0.005)
    return condition()


def make(qapp, folder, dirty=True, launcher=None):
    launched = []

    def launch(initial, *, read_root, write_root, idle=False, **_):
        assert _.get("extra_args") == ("--raw-workspace",)
        launched.append((initial, read_root, idle))
        return Proc(read_root, dirty)

    mover = Mover()
    host = EditorHost(launcher=launcher or launch, mover=mover, detector=lambda: True, parent=qapp)
    host._parent_hwnd = 5
    return host, mover, launched


def test_opening_starts_the_editor_hidden_opens_the_photo_and_puts_its_window_in_ours(qapp, folder):
    host, mover, launched = make(qapp, folder)
    seen = []
    host.opened.connect(seen.append)
    host.setGeometry(0, 44, 1440, 856)
    host.openPath(str(folder / "a.jpg"))
    assert host.state == STARTING
    assert wait(qapp, lambda: host.state == EDITING)
    assert launched == [(None, str(folder), True)] and seen == [str(folder / "a.jpg")]
    assert host._proc.control.opened == [("a.jpg", True)]                      # relative to the folder the editor reads, replacing what was open
    assert host._proc.control.calls[-1] == ("ui.cameraRaw", {"open": True})
    assert host._proc.control.calls[0][0] == "app.bind"
    assert ("attach", 77, 5) in mover.calls and ("place", 77, 0, 44, 1440, 856) in mover.calls and ("show", 77, True) in mover.calls
    host.setGeometry(0, 44, 1000, 600)                                        # the stage moved: the window follows
    assert mover.calls[-1] == ("place", 77, 0, 44, 1000, 600)


def test_closing_saves_a_changed_photo_hides_the_window_and_keeps_the_editor_for_the_next_photo(qapp, folder):
    host, mover, launched = make(qapp, folder)
    closed = []
    host.closed.connect(closed.append)
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    proc = host._proc
    host.close()
    assert wait(qapp, lambda: host.state == IDLE)
    assert proc.control.saved == 1 and closed == [str(folder / "a.jpg")] and ("show", 77, False) in mover.calls
    assert not proc.stopped
    host.openPath(str(folder / "a.jpg"))                                      # the same running editor is used again
    assert wait(qapp, lambda: host.state == EDITING)
    assert host._proc is proc and len(launched) == 1


def test_an_untouched_photo_is_not_saved_again(qapp, folder):
    host, mover, launched = make(qapp, folder, dirty=False)
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    proc = host._proc
    host.close()
    assert wait(qapp, lambda: host.state == IDLE)
    assert proc.control.saved == 0


def test_a_photo_in_another_folder_gets_a_fresh_editor_because_the_old_one_cannot_read_it(qapp, folder, tmp_path):
    host, mover, launched = make(qapp, folder)
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    first = host._proc
    host.close()
    assert wait(qapp, lambda: host.state == IDLE)
    other = tmp_path / "other"
    other.mkdir()
    (other / "b.jpg").write_bytes(b"x")
    host.openPath(str(other / "b.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    assert first.stopped and len(launched) == 2 and launched[1][1] == str(other)


def test_a_launch_that_fails_is_reported_and_leaves_the_editor_idle(qapp, folder):
    def broken(*args, **kwargs):
        raise RuntimeError("No compatible PhotoCraft build found.")

    host, mover, launched = make(qapp, folder, launcher=broken)
    seen = []
    host.failed.connect(seen.append)
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: bool(seen))
    assert seen == ["No compatible PhotoCraft build found."] and host.state == IDLE and host.error == "No compatible PhotoCraft build found."


def test_without_the_editor_installed_it_says_so_and_does_not_start(qapp, folder):
    host = EditorHost(detector=lambda: False, mover=Mover(), parent=qapp)
    seen = []
    host.failed.connect(seen.append)
    host.openPath(str(folder / "a.jpg"))
    assert seen and "not installed" in seen[0] and host.state == IDLE and host.available is False


def test_shutdown_stops_a_running_editor(qapp, folder):
    host, mover, launched = make(qapp, folder)
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    proc = host._proc
    host.shutdown()
    assert proc.stopped


def test_the_stage_is_hidden_until_the_editor_is_up_and_reports_where_the_window_goes(qapp, tmp_path, folder):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1200)
    window.setHeight(800)
    window.show()
    stage = window.findChild(QObject, "editorStage")
    assert stage is not None and not stage.property("visible")
    mover = Mover()
    host = EditorHost(mover=mover, detector=lambda: True, launcher=lambda initial, *, read_root, write_root, idle=False, **_: Proc(read_root), parent=qapp)
    engine.rootContext().setContextProperty("editor", host)
    pump(qapp, 0.2)
    host._parent_hwnd = 9
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    pump(qapp, 0.4)
    assert stage.property("visible")
    places = [call for call in mover.calls if call[0] == "place"]
    d = window.screen().devicePixelRatio()
    assert places and places[-1][2:] == (0, 0, round(window.width() * d), round(window.height() * d))
    window.close()


def test_raw_open_supplies_recipe_targets_and_restores_saved_recipe(qapp, folder, monkeypatch):
    from image_triage import photocraft_bridge as bridge
    recipe = {"version": 1, "cameraRaw": {"exposure": 0.5}}
    monkeypatch.setattr(bridge, "load_recipe", lambda path: recipe)
    host, mover, launched = make(qapp, folder)
    path = str(folder / "raw.nef")
    host.openPath(path)
    assert wait(qapp, lambda: host.state == EDITING)
    spec = host._proc.control.raw_specs[0]
    assert spec["recipe"] == recipe
    assert spec["recipePath"] == bridge.recipe_path(path).name
    assert spec["displayPath"] == bridge.display_render_path(path).name


def test_close_settles_camera_raw_and_save_failure_keeps_editor_open(qapp, folder):
    host, mover, launched = make(qapp, folder)
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    control = host._proc.control
    closed = []
    host.closed.connect(closed.append)

    def fail_save():
        raise OSError("Synthetic disk failure")

    control.app_save = fail_save
    host.close()
    assert wait(qapp, lambda: bool(host.error))
    assert host.state == EDITING and not closed
    assert control.calls[-1] == ("ui.cameraRaw", {"done": True})
    assert ("show", 77, False) not in mover.calls


def test_space_during_launch_closes_after_open_without_flashing_editor(qapp, folder):
    import threading
    ready = threading.Event()

    def launch(initial, **kwargs):
        assert ready.wait(3)
        return Proc(kwargs["read_root"])

    host, mover, launched = make(qapp, folder, launcher=launch)
    host.openPath(str(folder / "a.jpg"))
    host.close()
    ready.set()
    assert wait(qapp, lambda: host.state == IDLE)
    assert ("show", 77, True) not in mover.calls


def test_space_closes_editor_and_preview_to_grid(qapp, tmp_path, folder):
    engine, window, warnings = _load_main(qapp, tmp_path)
    host, mover, launched = make(qapp, folder)
    engine.rootContext().setContextProperty("editor", host)
    photos = engine.rootContext().contextProperty("photos")
    photos.setRows([{"path": str(folder / "a.jpg"), "name": "a.jpg", "ext": "JPG", "rating": 0, "decision": ""}], str(folder))
    popout = engine.rootContext().contextProperty("popout")
    popout.open(0)
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    QTest.keyClick(window, Qt.Key_Space)
    assert wait(qapp, lambda: host.state == IDLE and not popout.isOpen)
    assert host._proc.control.saved == 1
    window.close()


def test_switching_saves_before_replacing_and_keeps_one_editor(qapp, folder):
    host, mover, launched = make(qapp, folder)
    host.openPath(str(folder / "a.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    proc = host._proc
    events = []
    save, open_photo = proc.control.app_save, proc.control.app_open

    def saving():
        events.append("save")
        return save()

    def opening(*args, **kwargs):
        events.append("open")
        return open_photo(*args, **kwargs)

    proc.control.app_save, proc.control.app_open = saving, opening
    host.openPath(str(folder / "b.jpg"))
    assert wait(qapp, lambda: host.state == EDITING)
    assert events == ["save", "open"]
    assert host._proc is proc and len(launched) == 1
    assert host.path == str(folder / "b.jpg")


def test_failed_save_during_navigation_does_not_replace_photo(qapp, folder):
    host, mover, launched = make(qapp, folder)
    original = str(folder / "a.jpg")
    host.openPath(original)
    assert wait(qapp, lambda: host.state == EDITING)
    proc = host._proc

    def fail_save():
        raise OSError("Synthetic save failure")

    proc.control.app_save = fail_save
    host.openPath(str(folder / "b.jpg"))
    assert wait(qapp, lambda: bool(host.error))
    assert host.state == EDITING and host.path == original
    assert proc.control.opened == [("a.jpg", True)]
