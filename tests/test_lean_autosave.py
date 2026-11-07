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
from image_triage.models import ImageRecord
from image_triage.scanner import discover_edited_paths


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])




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




















@pytest.fixture
def shutdowns(monkeypatch):
    """The editor processes that were quit (the process object is a slots dataclass, so the method is patched on the class)."""
    quit_ones = []
    monkeypatch.setattr(bridge.PhotoCraftProcess, "shutdown", lambda self: quit_ones.append(self))
    return quit_ones


































# --- the render is written after a pause in editing ---------------------------------------------------------











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
    assert bridge.full_render_path(photo) is None


def test_the_screen_sized_render_is_all_a_photo_with_only_a_recipe_or_a_lean_autosave_has(tmp_path):
    photo, png, display = renders(tmp_path)
    write(display, 10, 1000.0)
    assert bridge.full_render_path(photo) is None


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
    assert bridge.full_render_path(photo) is None
    write(png, 10, 500.0)  # an older full-size one beside a usable screen-sized one
    assert bridge.full_render_path(photo) is None


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


def test_a_full_size_export_requests_editor_pixels_instead_of_display_decode(tmp_path, monkeypatch):
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
    from image_triage import edited_export
    image = QImage(120, 80, QImage.Format.Format_RGBA64)
    full = Mock(return_value=edited_export.EditedImage(image, None))
    monkeypatch.setattr(edited_export, "edited_image_for_export", full)
    assert module.render_edited_image(photo).size() == image.size()
    assert loaded == []
    write(png, 10, 1062.0)
    assert module.render_edited_image(photo).size() == image.size()
    assert loaded == []
    assert full.call_count == 2
