"""While the editor is up, an autosave writes the project and the screen-sized JPEG; the full-size PNG (211 MB for a 45 MP photo, and the heaviest
encode of the save) is written when the photo is left. Readers that want full size take the PNG only while it is not older than the JPEG."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage import preview_controller as controller_module
from image_triage.models import ImageRecord
from image_triage.preview_controller import PreviewController
from image_triage.scanner import discover_edited_paths


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(qapp):
    parent = QObject()
    ctl = PreviewController(parent)
    ctl._parent_keepalive = parent
    yield ctl
    ctl._photocraft_save_timer.stop()
    ctl._photocraft_dwell_timer.stop()
    ctl._photocraft_executor.shutdown(wait=True)
    ctl._photocraft_preview_executor.shutdown(wait=True)
    qapp.processEvents()


def editing(tmp_path, *, lean_supported: bool, revision: int = 5, opened: int = 1):
    """A running editor with a photo open for editing: ``opened`` is the revision it was opened at, ``revision`` where it is now."""
    write_root = tmp_path / ".image_triage_edits"
    write_root.mkdir()
    control = Mock()
    control.document_revision.return_value = revision
    control.stash_without_preview_supported = lean_supported
    control.call.return_value = {"pending": True, "job": 3}
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=1, token_file=tmp_path / "token", read_root=tmp_path, write_root=write_root,
    )
    proc.process.poll.return_value = None
    proc.source_path = str(tmp_path / "IMG.NEF")
    proc.current_sidecar = write_root / "IMG.NEF.pcraft"
    proc.opened_revision = opened
    proc.camera_raw_first = False
    return proc


def stashes(proc):
    return [call.args[1] for call in proc.control.call.call_args_list if call.args[0] == "app.stash"]


def test_an_autosave_leaves_the_full_size_render_out_when_the_editor_can_save_without_it(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True)
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, lean=True)
    (params,) = stashes(proc)
    assert "preview" not in params
    assert params["display"] == "IMG.NEF.photocraft.display.jpg"
    assert params["path"] == "IMG.NEF.pcraft"
    assert str(proc.current_sidecar) in proc.preview_missing


def test_an_editor_that_cannot_save_without_the_render_is_asked_for_it_as_before(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=False)
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, lean=True)
    (params,) = stashes(proc)
    assert params["preview"] == "IMG.NEF.photocraft.png"
    assert not proc.preview_missing


def test_a_capability_that_is_merely_truthy_is_not_taken_for_support(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True)
    proc.control.stash_without_preview_supported = Mock()  # what an attribute of a stand-in control looks like
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, lean=True)
    assert "preview" in stashes(proc)[0]


def test_leaving_the_photo_asks_for_the_full_size_render(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True)
    controller._save_photocraft_sidecar_if_dirty(proc)
    (params,) = stashes(proc)
    assert params["preview"] == "IMG.NEF.photocraft.png"
    assert not proc.preview_missing


def test_leaving_writes_the_render_that_an_autosave_left_out_even_if_nothing_changed_since(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True)
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, lean=True)
    proc.control.call.reset_mock()
    proc.pending_stashes.clear()
    proc.opened_revision = 5  # the autosave brought it up to the current revision
    controller._save_photocraft_sidecar_if_dirty(proc)
    (params,) = stashes(proc)
    assert "preview" in params
    assert not proc.preview_missing, "and it is done: leaving again saves nothing"
    proc.control.call.reset_mock()
    proc.pending_stashes.clear()
    controller._save_photocraft_sidecar_if_dirty(proc)
    assert stashes(proc) == []


def test_an_autosave_of_a_photo_nothing_changed_in_saves_nothing(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True, revision=5, opened=5)
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, lean=True)
    assert stashes(proc) == []
    assert not proc.preview_missing


def test_a_photo_left_without_a_pending_render_and_unchanged_saves_nothing(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True, revision=5, opened=5)
    controller._save_photocraft_sidecar_if_dirty(proc)
    assert stashes(proc) == []


def test_more_edits_are_autosaved_lean_again(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True)
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, lean=True)
    proc.pending_stashes.clear()
    proc.control.document_revision.return_value = 9
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, lean=True)
    assert len(stashes(proc)) == 2 and all("preview" not in params for params in stashes(proc))


def test_the_autosave_timer_path_is_the_lean_one(controller, tmp_path, monkeypatch):
    proc = editing(tmp_path, lean_supported=True)
    controller._photocraft = proc
    monkeypatch.setattr(controller, "_sync_photocraft_theme", lambda proc: None)
    monkeypatch.setattr(controller, "_poll_photocraft_stashes", lambda proc: None)
    controller._autosave_photocraft()
    (params,) = stashes(proc)
    assert "preview" not in params


@pytest.fixture
def shutdowns(monkeypatch):
    """The editor processes that were quit (the process object is a slots dataclass, so the method is patched on the class)."""
    quit_ones = []
    monkeypatch.setattr(bridge.PhotoCraftProcess, "shutdown", lambda self: quit_ones.append(self))
    return quit_ones


def test_the_saves_the_ui_thread_waits_for_are_lean(controller, tmp_path):
    """Closing the popout and quitting wait on the UI thread: not for the 211 MB render too."""
    proc = editing(tmp_path, lean_supported=True)
    controller._photocraft = proc
    controller._wait_photocraft_stashes = lambda proc, only=None: None
    controller._save_active_photocraft()
    (params,) = stashes(proc)
    assert "preview" not in params and str(proc.current_sidecar) in proc.preview_missing


def test_quitting_saves_lean_and_leaves_the_render_behind(controller, tmp_path, shutdowns):
    proc = editing(tmp_path, lean_supported=True)
    controller._photocraft = proc
    controller._wait_photocraft_stashes = lambda proc, only=None: None
    controller.shutdown_photocraft()
    (params,) = stashes(proc)
    assert "preview" not in params
    assert shutdowns == [proc]
    assert controller._photocraft is None


def test_closing_the_popout_saves_what_changed_lean_and_quits_the_editor_at_once(controller, tmp_path, monkeypatch, shutdowns):
    """An editor under a popout that has been hidden does not answer, so nothing can be left for after the close: the UI thread waits for
    the save, and the save leaves the 211 MB render out."""
    proc = editing(tmp_path, lean_supported=True)
    controller._photocraft = proc
    controller._wait_photocraft_stashes = lambda proc, only=None: None
    assert controller.prepare_photocraft_preview_close() is True
    (params,) = stashes(proc)
    assert "preview" not in params
    assert shutdowns == [proc], "the editor is quit while its window is still there"
    assert controller._photocraft is None


def test_closing_the_popout_with_the_render_behind_does_not_write_it(controller, tmp_path, shutdowns):
    proc = editing(tmp_path, lean_supported=True, revision=5, opened=5)
    proc.preview_missing.add(str(proc.current_sidecar))
    controller._photocraft = proc
    controller._wait_photocraft_stashes = lambda proc, only=None: None
    controller.prepare_photocraft_preview_close()
    assert stashes(proc) == [] and shutdowns == [proc]


# --- the render is written after a pause in editing ---------------------------------------------------------

def autosave_tick(controller, proc, monkeypatch, now):
    monkeypatch.setattr(controller_module.time, "monotonic", lambda: now)
    monkeypatch.setattr(controller, "_sync_photocraft_theme", lambda proc: None)
    monkeypatch.setattr(controller, "_poll_photocraft_stashes", lambda proc: proc.pending_stashes.clear())
    controller._photocraft = proc
    controller._autosave_photocraft()


def test_the_render_is_written_once_the_photo_has_been_left_alone_for_a_while(controller, tmp_path, monkeypatch):
    proc = editing(tmp_path, lean_supported=True)
    autosave_tick(controller, proc, monkeypatch, now=1000.0)  # the edit: saved lean
    assert [("preview" in params) for params in stashes(proc)] == [False]
    proc.opened_revision = 5
    autosave_tick(controller, proc, monkeypatch, now=1000.0 + controller.RENDER_IDLE_S - 1)
    assert len(stashes(proc)) == 1, "not yet: it could be a moment between two edits"
    autosave_tick(controller, proc, monkeypatch, now=1000.0 + controller.RENDER_IDLE_S + 1)
    assert [("preview" in params) for params in stashes(proc)] == [False, True]
    assert not proc.preview_missing
    autosave_tick(controller, proc, monkeypatch, now=1000.0 + controller.RENDER_IDLE_S + 30)
    assert len(stashes(proc)) == 2, "and once"


def test_another_edit_starts_the_wait_again(controller, tmp_path, monkeypatch):
    proc = editing(tmp_path, lean_supported=True)
    autosave_tick(controller, proc, monkeypatch, now=1000.0)
    proc.control.document_revision.return_value = 9  # edited again, seven seconds later
    autosave_tick(controller, proc, monkeypatch, now=1007.0)
    assert [("preview" in params) for params in stashes(proc)] == [False, False]
    proc.opened_revision = 9
    autosave_tick(controller, proc, monkeypatch, now=1007.0 + controller.RENDER_IDLE_S - 1)
    assert len(stashes(proc)) == 2
    autosave_tick(controller, proc, monkeypatch, now=1007.0 + controller.RENDER_IDLE_S + 1)
    assert [("preview" in params) for params in stashes(proc)] == [False, False, True]


def test_a_photo_nothing_was_done_to_in_this_session_gets_no_render_written_while_it_is_looked_at(controller, tmp_path, monkeypatch):
    """Its render may be behind from an earlier session; writing it takes seconds of the editor's time, and it is caught up when the photo is left."""
    proc = editing(tmp_path, lean_supported=True, revision=5, opened=5)
    project_and_render(tmp_path, 1060.0, 1000.0)
    assert bridge.render_is_behind(proc.source_path)
    autosave_tick(controller, proc, monkeypatch, now=5000.0)
    autosave_tick(controller, proc, monkeypatch, now=9000.0)
    assert stashes(proc) == []


def test_the_render_is_not_written_while_the_last_save_is_still_running(controller, tmp_path, monkeypatch):
    proc = editing(tmp_path, lean_supported=True)
    autosave_tick(controller, proc, monkeypatch, now=1000.0)
    proc.opened_revision = 5
    proc.pending_stashes[str(proc.current_sidecar)] = (3, proc.source_path)
    monkeypatch.setattr(controller, "_poll_photocraft_stashes", lambda proc: None)  # the job has not finished
    waited = []
    monkeypatch.setattr(controller, "_wait_photocraft_stashes", lambda proc, only=None: waited.append(only))
    monkeypatch.setattr(controller_module.time, "monotonic", lambda: 1000.0 + controller.RENDER_IDLE_S + 5)
    controller._autosave_photocraft()
    assert len(stashes(proc)) == 1
    assert waited == [], "the autosave does not wait for a save that is still running: it tries again at the next tick"


# --- a render that is behind according to the files --------------------------------------------------------------

def project_and_render(tmp_path, project_at: float, render_at: float | None):
    write_root = tmp_path / ".image_triage_edits"
    write_root.mkdir(exist_ok=True)
    photo = str(tmp_path / "IMG.NEF")
    write(bridge.sidecar_pcraft_path(photo), 10, project_at)
    if render_at is not None:
        write(bridge.rendered_preview_path(photo), 10, render_at)
    return photo


def test_a_render_older_than_its_project_is_behind(tmp_path):
    assert bridge.render_is_behind(project_and_render(tmp_path, 1060.0, 1000.0))


def test_a_missing_render_beside_a_project_is_behind(tmp_path):
    assert bridge.render_is_behind(project_and_render(tmp_path, 1060.0, None))


def test_a_render_written_with_its_project_is_not_behind(tmp_path):
    photo = project_and_render(tmp_path, 1000.0, 1008.0)  # the render lands seconds after the project
    assert not bridge.render_is_behind(photo)


def test_a_render_within_the_tolerance_of_its_project_is_not_behind(tmp_path):
    assert not bridge.render_is_behind(project_and_render(tmp_path, 1001.0, 1000.0))


def test_a_photo_with_no_project_has_nothing_behind(tmp_path):
    """A photo with only a Camera Raw recipe has a screen-sized render and no project, so no full-size render to be behind."""
    photo = str(tmp_path / "IMG.NEF")
    write(bridge.display_render_path(photo), 10, 1000.0)
    assert not bridge.render_is_behind(photo)
    assert not bridge.render_is_behind(str(tmp_path / "other.NEF"))


def test_leaving_a_photo_whose_render_an_earlier_session_left_behind_catches_it_up(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True, revision=5, opened=5)
    project_and_render(tmp_path, 1060.0, 1000.0)
    assert not proc.preview_missing, "nothing is remembered in this session"
    controller._save_photocraft_sidecar_if_dirty(proc)
    (params,) = stashes(proc)
    assert "preview" in params


def test_leaving_a_photo_nothing_was_done_to_saves_nothing(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True, revision=5, opened=5)  # no project on disk
    controller._save_photocraft_sidecar_if_dirty(proc)
    assert stashes(proc) == []


# --- the capability -----------------------------------------------------------------------------------------------

def handoff_control(capabilities):
    control = object.__new__(bridge.PhotoCraftControl)

    def call(method, params=None):
        if method == "app.handoff":
            return capabilities
        message = "stash requires path and preview or display" if method == "app.stash" else "bind requires an open document, .pcraft path and .png preview"
        raise bridge.PhotoCraftError(f"PhotoCraft control {method!r} failed: {message}")

    control.call = call
    return control


def test_the_handoff_says_whether_the_editor_saves_without_the_render():
    with_it = handoff_control({"version": 2, "stashWithoutPreview": 1})
    with_it.require_hosted_handoff()
    assert with_it.stash_without_preview_supported is True
    without = handoff_control({"version": 2})
    without.require_hosted_handoff()
    assert without.stash_without_preview_supported is False


# --- the readers of the render ----------------------------------------------------------------------------------------

def write(path: Path, size: int, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))


def renders(tmp_path):
    photo = str(tmp_path / "IMG.NEF")
    return photo, bridge.rendered_preview_path(photo), bridge.display_render_path(photo)


def test_the_full_size_render_is_taken_when_it_came_with_the_same_save(tmp_path):
    photo, png, display = renders(tmp_path)
    write(display, 10, 1000.0)
    write(png, 10, 1008.0)  # written when the whole save landed, seconds after the JPEG
    assert bridge.full_render_path(photo) == png


def test_the_full_size_render_is_not_taken_once_a_later_autosave_wrote_only_the_screen_sized_one(tmp_path):
    photo, png, display = renders(tmp_path)
    write(png, 10, 1000.0)
    write(display, 10, 1060.0)
    assert bridge.full_render_path(photo) == display


def test_the_screen_sized_render_is_all_a_photo_with_only_a_recipe_or_a_lean_autosave_has(tmp_path):
    photo, png, display = renders(tmp_path)
    write(display, 10, 1000.0)
    assert bridge.full_render_path(photo) == display


def test_the_full_size_render_is_all_an_older_save_has(tmp_path):
    photo, png, display = renders(tmp_path)
    write(png, 10, 1000.0)
    assert bridge.full_render_path(photo) == png


def test_an_empty_or_missing_render_is_not_one(tmp_path):
    photo, png, display = renders(tmp_path)
    assert bridge.full_render_path(photo) is None
    write(png, 0, 1000.0)
    write(display, 0, 1000.0)
    assert bridge.full_render_path(photo) is None
    write(display, 10, 1000.0)
    assert bridge.full_render_path(photo) == display
    write(png, 10, 500.0)  # an older full-size one beside a usable screen-sized one
    assert bridge.full_render_path(photo) == display


def test_the_edited_version_the_grid_offers_follows_the_same_rule(tmp_path):
    photo = tmp_path / "IMG.NEF"
    photo.write_bytes(b"x")
    stat = photo.stat()
    record = ImageRecord(path=str(photo), name="IMG.NEF", size=stat.st_size, modified_ns=stat.st_mtime_ns)
    _photo, png, display = renders(tmp_path)
    write(png, 10, 1000.0)
    assert discover_edited_paths(record)[0] == str(png)
    write(display, 10, 1060.0)
    assert discover_edited_paths(record)[0] == str(display), "not a full-size render the autosave has since passed"
    write(png, 10, 1062.0)
    assert discover_edited_paths(record)[0] == str(png), "until the leaving save brings it up to date"


def test_a_full_size_export_of_edits_uses_the_same_rule(tmp_path, monkeypatch):
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImage

    from image_triage import edit_render_headless as module

    photo, png, display = renders(tmp_path)
    write(png, 10, 1000.0)
    write(display, 10, 1060.0)
    loaded = []

    def fake_load(path, size, prefer_embedded=False):
        loaded.append(path)
        image = QImage(4, 4, QImage.Format.Format_RGB32)
        image.fill(0xFF336699)
        return image, None

    import image_triage.imaging as imaging

    monkeypatch.setattr(imaging, "load_image_for_display", fake_load)
    module.render_edited_image(photo)  # no target size: full resolution
    assert loaded == [str(display)]
    loaded.clear()
    write(png, 10, 1062.0)
    module.render_edited_image(photo)
    assert loaded == [str(png)]
