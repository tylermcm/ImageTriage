"""After a photo opens in Camera Raw the editor is asked to develop the next raw, so stepping to it costs next to nothing.
The editor is a stand-in (`test_qml_shell_editor.Proc`); the real one is not started."""
from __future__ import annotations

import time
from types import SimpleNamespace

from image_triage import photocraft_bridge as bridge
from image_triage.qml_shell.editor_host import EDITING
from test_qml_shell import qapp  # noqa: F401
from test_qml_shell_editor import folder, make, wait  # noqa: F401


class Browsed:
    """The order the photos are browsed in, as the editor's filmstrip holds it."""

    def __init__(self, paths):
        self.paths = [str(p) for p in paths]

    def row_for_path(self, path):
        return self.paths.index(path) if path in self.paths else -1

    def row_at(self, index):
        return {"path": self.paths[index]} if 0 <= index < len(self.paths) else None


def launcher_with(capable=True, workspace=True):
    """A launcher whose editor says whether it can develop ahead, and records what it is asked."""
    from test_qml_shell_editor import Proc

    made = []

    def launch(path, read_root, write_root, **_):
        proc = Proc(read_root)
        proc.camera_raw_first = workspace
        proc.prefetch_sent = None
        proc.control.camera_raw_prefetch_supported = capable
        proc.control.prefetched = []
        proc.control.app_prefetch = lambda relative, signature: proc.control.prefetched.append((relative, signature)) or {"queued": True}
        made.append(proc)
        return proc

    return launch, made


def shoot(folder, names):  # noqa: F811
    paths = []
    for name in names:
        path = folder / name
        path.write_bytes(b"raw " + name.encode())
        paths.append(path)
    return paths


def opened(qapp, folder, names, start, **editor):  # noqa: F811
    paths = shoot(folder, names)
    launch, made = launcher_with(**editor)
    host, _mover, _launched = make(qapp, folder, launcher=launch)
    host._filmstrip = SimpleNamespace(photos=Browsed(paths), stop=lambda: None)
    host.openPath(str(folder / start))
    assert wait(qapp, lambda: host.state == EDITING)
    return host, made[0], paths


def settle(qapp, done=lambda: False):  # noqa: F811
    end = time.monotonic() + 1.0
    while time.monotonic() < end and not done():
        qapp.processEvents()
        time.sleep(0.01)


def test_the_next_raw_is_asked_for_with_its_signature_once_the_photo_is_open(qapp, folder):  # noqa: F811
    host, proc, paths = opened(qapp, folder, ["1.NEF", "2.NEF", "3.NEF"], "1.NEF")
    settle(qapp, lambda: proc.control.prefetched)
    assert proc.control.prefetched == [("2.NEF", bridge.file_signature(str(paths[1])))]
    assert proc.prefetch_sent[0] == "2.NEF"
    host.shutdown()


def test_photos_that_are_not_raw_are_skipped_and_the_search_does_not_go_far(qapp, folder):  # noqa: F811
    host, proc, paths = opened(qapp, folder, ["1.NEF", "a.jpg", "b.jpg", "4.NEF"], "1.NEF")
    settle(qapp, lambda: proc.control.prefetched)
    assert [relative for relative, _signature in proc.control.prefetched] == ["4.NEF"]     # within three photos
    host.shutdown()

    host, proc, paths = opened(qapp, folder, ["1.CR2", "a.png", "b.png", "c.png", "5.CR2"], "1.CR2")
    settle(qapp)
    assert proc.control.prefetched == []                                                   # the next raw is too far to guess at
    host.shutdown()


def test_nothing_is_asked_at_the_end_of_the_folder_or_of_an_editor_that_cannot(qapp, folder):  # noqa: F811
    host, proc, _paths = opened(qapp, folder, ["1.NEF", "2.NEF"], "2.NEF")
    settle(qapp)
    assert proc.control.prefetched == []
    host.shutdown()
    for editor in ({"capable": False}, {"workspace": False}):
        host, proc, _paths = opened(qapp, folder, ["1.NEF", "2.NEF"], "1.NEF", **editor)
        settle(qapp)
        assert proc.control.prefetched == [], editor
        host.shutdown()


def test_a_photo_that_opens_as_a_project_is_not_developed_ahead(qapp, folder):  # noqa: F811
    paths = shoot(folder, ["1.NEF", "2.NEF"])
    sidecar = bridge.sidecar_pcraft_path(str(paths[1]))
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_bytes(b"project")
    launch, made = launcher_with()
    host, _mover, _launched = make(qapp, folder, launcher=launch)
    host._filmstrip = SimpleNamespace(photos=Browsed(paths), stop=lambda: None)
    host.openPath(str(paths[0]))
    assert wait(qapp, lambda: host.state == EDITING)
    settle(qapp)
    assert made[0].control.prefetched == []
    host.shutdown()


def test_a_request_that_fails_is_only_logged(qapp, folder, caplog):  # noqa: F811
    paths = shoot(folder, ["1.NEF", "2.NEF"])
    launch, made = launcher_with()
    host, _mover, _launched = make(qapp, folder, launcher=launch)
    host._filmstrip = SimpleNamespace(photos=Browsed(paths), stop=lambda: None)
    failures = []
    host.failed.connect(failures.append)
    real = launch

    def broken(path, read_root, write_root, **kwargs):
        proc = real(path, read_root, write_root, **kwargs)
        proc.control.app_prefetch = lambda *args: (_ for _ in ()).throw(bridge.PhotoCraftError("the editor is busy"))
        return proc

    host._launcher = broken
    with caplog.at_level("INFO"):
        host.openPath(str(paths[0]))
        assert wait(qapp, lambda: host.state == EDITING)
        settle(qapp, lambda: "get 2.NEF ready" in caplog.text)
    assert "the editor is busy" in caplog.text and failures == [] and host.state == EDITING and not host.error
    host.shutdown()


def test_nothing_is_browsed_then_nothing_is_guessed(qapp, folder):  # noqa: F811
    shoot(folder, ["1.NEF", "2.NEF"])
    launch, made = launcher_with()
    host, _mover, _launched = make(qapp, folder, launcher=launch)
    host.openPath(str(folder / "1.NEF"))
    assert wait(qapp, lambda: host.state == EDITING)
    settle(qapp)
    assert made[0].control.prefetched == []
    host.shutdown()
