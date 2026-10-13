"""While the editor is up, an autosave writes the project and the screen-sized JPEG; the full-size PNG (211 MB for a 45 MP photo, and the heaviest
encode of the save) is written when the photo is left. Readers that want full size take the PNG only while it is not older than the JPEG."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
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
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, autosave=True)
    (params,) = stashes(proc)
    assert "preview" not in params
    assert params["display"] == "IMG.NEF.photocraft.display.jpg"
    assert params["path"] == "IMG.NEF.pcraft"
    assert str(proc.current_sidecar) in proc.preview_missing


def test_an_editor_that_cannot_save_without_the_render_is_asked_for_it_as_before(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=False)
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, autosave=True)
    (params,) = stashes(proc)
    assert params["preview"] == "IMG.NEF.photocraft.png"
    assert not proc.preview_missing


def test_a_capability_that_is_merely_truthy_is_not_taken_for_support(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True)
    proc.control.stash_without_preview_supported = Mock()  # what an attribute of a stand-in control looks like
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, autosave=True)
    assert "preview" in stashes(proc)[0]


def test_leaving_the_photo_asks_for_the_full_size_render(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True)
    controller._save_photocraft_sidecar_if_dirty(proc)
    (params,) = stashes(proc)
    assert params["preview"] == "IMG.NEF.photocraft.png"
    assert not proc.preview_missing


def test_leaving_writes_the_render_that_an_autosave_left_out_even_if_nothing_changed_since(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True)
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, autosave=True)
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
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, autosave=True)
    assert stashes(proc) == []
    assert not proc.preview_missing


def test_a_photo_left_without_a_pending_render_and_unchanged_saves_nothing(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True, revision=5, opened=5)
    controller._save_photocraft_sidecar_if_dirty(proc)
    assert stashes(proc) == []


def test_more_edits_are_autosaved_lean_again(controller, tmp_path):
    proc = editing(tmp_path, lean_supported=True)
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, autosave=True)
    proc.pending_stashes.clear()
    proc.control.document_revision.return_value = 9
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, autosave=True)
    assert len(stashes(proc)) == 2 and all("preview" not in params for params in stashes(proc))


def test_the_autosave_timer_path_is_the_lean_one(controller, tmp_path, monkeypatch):
    proc = editing(tmp_path, lean_supported=True)
    controller._photocraft = proc
    monkeypatch.setattr(controller, "_sync_photocraft_theme", lambda proc: None)
    monkeypatch.setattr(controller, "_poll_photocraft_stashes", lambda proc: None)
    controller._autosave_photocraft()
    (params,) = stashes(proc)
    assert "preview" not in params


def test_the_other_ways_of_saving_the_open_photo_write_the_render(controller, tmp_path):
    for method in ("_save_active_photocraft", "_shutdown_photocraft"):
        base = tmp_path / method
        base.mkdir()
        proc = editing(base, lean_supported=True)
        controller._photocraft = proc
        proc.pending_stashes.clear()
        controller._wait_photocraft_stashes = lambda proc, only=None: None
        getattr(controller, method)()
        assert "preview" in stashes(proc)[0], method


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
