"""The filmstrip keeps the thumbnails that stay on screen when the photo changes, and repaints only what changed: rebuilding every thumb on
each step was a flash, and it cost more than flipping through the photos themselves."""
from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from image_triage.preview_controller import PreviewController
from image_triage.ui.preview_studio import Filmstrip, FilmstripThumb


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def pixmap(color: str) -> QPixmap:
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return QPixmap.fromImage(image)


class Library:
    """What the strip is told about each frame: a picture (when one has arrived) and a tag."""

    def __init__(self) -> None:
        self.pictures: dict[int, QPixmap] = {}
        self.tags: dict[int, str | None] = {}

    def picture(self, index: int) -> QPixmap | None:
        return self.pictures.get(index)

    def tag(self, index: int) -> str | None:
        return self.tags.get(index)


@pytest.fixture
def paints(monkeypatch):
    """The frame index of every thumb painted from now on (patched before the strip is built: Qt remembers what a widget overrides)."""
    painted: list[int] = []
    original = FilmstripThumb.paintEvent

    def counting(self, event):
        painted.append(self._index)
        original(self, event)

    monkeypatch.setattr(FilmstripThumb, "paintEvent", counting)
    return painted


@pytest.fixture
def strip(qapp, paints):
    library = Library()
    for index in range(100):
        library.pictures[index] = pixmap("#336699")
    widget = Filmstrip(focus="middle")
    widget.library = library
    widget.resize(1000, widget.height())
    widget.show()
    widget.set_source(100, 50, library.picture, library.tag)
    for _ in range(3):
        qapp.processEvents()
    paints.clear()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def on_screen(strip: Filmstrip) -> list[FilmstripThumb]:
    """The thumbs that are in the strip and not put away (a deleted widget lingers until the event loop reaches it, hidden)."""
    return [thumb for thumb in strip.findChildren(FilmstripThumb) if not thumb.isHidden()]


def test_stepping_to_the_next_photo_keeps_the_thumbs_that_stay_on_screen(strip, qapp):
    before = dict(strip._thumbs)
    strip.set_current(51)
    qapp.processEvents()
    after = dict(strip._thumbs)
    stayed = set(before) & set(after)
    assert len(stayed) >= strip._count - 2, "all but the thumb that left and the one that came in are kept"
    assert all(before[index] is after[index] for index in stayed), "the same widgets, not rebuilt copies"
    assert len(on_screen(strip)) == len(after), "the ones that left are gone, not left behind"


def test_the_ring_moves_to_the_new_photo_and_only_those_two_thumbs_change(strip, qapp, paints):
    old_thumb, new_thumb = strip._thumbs[50], strip._thumbs[51]
    assert old_thumb._current and not new_thumb._current
    strip.refresh()
    qapp.processEvents()
    paints.clear()
    strip.set_current(51)
    qapp.processEvents()
    assert strip._thumbs[50]._current is False and strip._thumbs[51]._current is True
    assert strip._thumbs[50] is old_thumb and strip._thumbs[51] is new_thumb


def test_refreshing_with_nothing_changed_repaints_no_thumb(strip, qapp, paints):
    qapp.processEvents()
    paints.clear()
    strip.refresh()
    qapp.processEvents()
    assert paints == []


def test_a_thumbnail_that_arrives_repaints_only_that_thumb(strip, qapp, paints):
    strip.library.pictures[53] = pixmap("#cc3333")
    qapp.processEvents()
    paints.clear()
    strip.refresh()
    qapp.processEvents()
    assert paints == [53]


def test_a_thumbnail_still_to_come_is_the_placeholder_and_does_not_repaint_again(strip, qapp, paints):
    strip.library.pictures.pop(52)
    strip.refresh()
    qapp.processEvents()
    assert strip._thumbs[52]._pixmap is None
    paints.clear()
    strip.refresh()
    qapp.processEvents()
    assert paints == []


def test_a_changed_tag_repaints_only_that_thumb(strip, qapp, paints):
    strip.library.tags[49] = "keep"
    strip.refresh()
    qapp.processEvents()
    assert strip._thumbs[49]._tag == "keep"
    assert paints == [49]


def test_show_state_reports_whether_it_repainted(strip):
    thumb = strip._thumbs[50]
    same = thumb._pixmap
    assert thumb.show_state(thumb._tag, thumb._current, same) is False
    assert thumb.show_state(thumb._tag, thumb._current, pixmap("#336699")) is True, "a different picture"
    assert thumb.show_state(thumb._tag, not thumb._current, thumb._pixmap) is True
    assert thumb.show_state("reject", thumb._current, thumb._pixmap) is True
    assert thumb.show_state("reject", thumb._current, None) is True, "the picture went away"
    assert thumb.show_state("reject", thumb._current, None) is False


def test_resizing_the_strip_rebuilds_thumbs_for_the_new_size(strip, qapp):
    before = dict(strip._thumbs)
    strip.resize(620, strip.height())
    qapp.processEvents()
    assert strip._thumbs
    assert all(thumb.height() == strip._thumb_h for thumb in strip._thumbs.values())
    assert sorted(strip._thumbs) == list(range(strip._offset, strip._offset + strip._count))
    del before


def test_a_collapsed_strip_drops_its_thumbs_when_refreshed_and_expanding_brings_them_back(strip, qapp):
    strip.toggle_collapsed()
    qapp.processEvents()
    assert all(not thumb.isVisible() for thumb in strip.findChildren(FilmstripThumb))
    strip.refresh()
    qapp.processEvents()
    assert strip._thumbs == {}
    assert not any(thumb for thumb in strip.findChildren(FilmstripThumb) if thumb.isVisible())
    strip.toggle_collapsed()
    qapp.processEvents()
    assert sorted(strip._thumbs) == list(range(strip._offset, strip._offset + strip._count))
    assert all(thumb.isVisible() for thumb in strip._thumbs.values())


def test_collapsing_and_expanding_without_a_refresh_keeps_the_same_thumbs(strip, qapp, paints):
    before = dict(strip._thumbs)
    strip.toggle_collapsed()
    qapp.processEvents()
    strip.toggle_collapsed()
    qapp.processEvents()
    assert all(strip._thumbs[index] is thumb for index, thumb in before.items() if index in strip._thumbs)
    assert len(on_screen(strip)) == len(strip._thumbs)
    assert all(thumb.isVisible() for thumb in strip._thumbs.values())


def test_scrolling_far_replaces_the_thumbs_and_leaves_none_stale(strip, qapp):
    strip.set_source(100, 5, strip.library.picture, strip.library.tag)
    qapp.processEvents()
    assert sorted(strip._thumbs) == list(range(strip._offset, strip._offset + strip._count))
    assert len(on_screen(strip)) == len(strip._thumbs)
    assert [index for index, thumb in strip._thumbs.items() if thumb._current] == [5]


def test_clicking_a_kept_thumb_still_selects_its_frame(strip, qapp):
    selected: list[int] = []
    strip.frame_selected.connect(selected.append)
    strip.set_current(51)
    qapp.processEvents()
    strip._thumbs[52].clicked.emit(52)
    assert selected[-1] == 52


# --- the pixmaps the controller hands the strip ---------------------------------------------------------

@pytest.fixture
def controller(qapp):
    parent = QObject()
    ctl = PreviewController(parent)
    yield ctl
    ctl._photocraft_save_timer.stop()
    ctl._photocraft_dwell_timer.stop()
    ctl._photocraft_executor.shutdown(wait=True)
    ctl._photocraft_preview_executor.shutdown(wait=True)
    qapp.processEvents()


def test_the_same_image_gives_the_same_pixmap(controller):
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    assert controller._filmstrip_pixmap(image) is controller._filmstrip_pixmap(image)
    other = QImage(40, 30, QImage.Format.Format_RGB32)
    other.fill(QColor("#cc3333"))
    assert controller._filmstrip_pixmap(other) is not controller._filmstrip_pixmap(image)


def test_a_thumb_shown_twice_from_the_same_image_is_not_repainted(controller, qapp):
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    thumb = FilmstripThumb(0, None, False, controller._filmstrip_pixmap(image))
    assert thumb.show_state(None, False, controller._filmstrip_pixmap(image)) is False


def test_the_pixmap_memo_is_bounded_and_forgets_the_oldest_first(controller):
    images = []
    for n in range(520):
        image = QImage(2, 2, QImage.Format.Format_RGB32)
        image.fill(QColor(n % 256, (n // 256) % 256, 0))
        images.append(image)
        controller._filmstrip_pixmap(image)
    assert len(controller._filmstrip_pixmaps) <= 512
    assert int(images[0].cacheKey()) not in controller._filmstrip_pixmaps
    assert int(images[-1].cacheKey()) in controller._filmstrip_pixmaps


# --- looking at the disk for each photo's edits ----------------------------------------------------------
# The memory of the edit states is ``EditStateMemo`` (tests/test_edit_state_memo.py); these are what the strip does with it.

class Looks:
    """Stands in for ``edit_state_for``: counts the lookups and the threads that made them."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.threads: list[threading.Thread] = []
        self.states: dict[str, tuple[int, int]] = {}

    def __call__(self, path):
        self.threads.append(threading.current_thread())
        self.paths.append(str(path))
        return self.states.get(str(path), (0, 0))


@pytest.fixture
def looks():
    return Looks()


@pytest.fixture
def popout(controller, looks):
    """A popout on screen that counts the times its strip is told to draw again, and a thumbnail manager's memory of edit states."""
    from unittest.mock import Mock

    from image_triage.cache import EditStateMemo

    preview = Mock()
    preview.isVisible.return_value = True
    controller._window = SimpleNamespace(
        _preview=preview, preview=preview, grid=Mock(), thumbnail_manager=SimpleNamespace(edit_states=EditStateMemo(probe=looks)),
    )
    return preview


def settle(qapp, controller, probes: int) -> None:
    """Waits until ``probes`` lookups have been made on the background thread."""
    memo = controller._window.thumbnail_manager.edit_states
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        qapp.processEvents()
        if len(memo._probe.paths) >= probes and not memo._pending:
            return
        time.sleep(0.002)
    raise AssertionError(f"lookups made: {memo._probe.paths}, still being looked up: {memo._pending}")


def test_the_strip_never_looks_at_the_disk_on_the_ui_thread(controller, looks, popout, qapp):
    looks.states["a.NEF"] = (5, 6)
    assert controller._filmstrip_edit_state("a.NEF") == (0, 0), "until the answer is found: no edits"
    settle(qapp, controller, 1)
    assert looks.threads[0] is not threading.main_thread()
    assert controller._filmstrip_edit_state("a.NEF") == (5, 6)
    assert looks.paths == ["a.NEF"]


def test_the_strip_is_drawn_again_when_an_answer_is_not_what_it_drew(controller, popout):
    controller.handle_edit_state_changed("edited.NEF")
    popout.refresh_filmstrip.assert_called_once()


def test_no_redraw_is_asked_of_a_popout_that_is_not_on_screen(controller, popout):
    popout.isVisible.return_value = False
    controller.handle_edit_state_changed("edited.NEF")
    popout.refresh_filmstrip.assert_not_called()


def test_a_saved_edit_makes_the_photo_s_edits_be_looked_at_again(controller, looks, popout, qapp):
    popout_grid = controller._window.grid
    memo = controller._window.thumbnail_manager.edit_states
    memo.exact("a.NEF")
    memo.exact("b.NEF")
    controller._refresh_photocraft_save("a.NEF")
    assert memo._known["a.NEF"][0] == float("-inf") and memo._known["b.NEF"][0] != float("-inf")
    popout_grid.update_items.assert_called_once()


def test_leaving_the_editor_makes_every_photo_s_edits_be_looked_at_again(controller, looks, popout, qapp):
    memo = controller._window.thumbnail_manager.edit_states
    memo.exact("a.NEF")
    memo.exact("b.NEF")
    controller.leave_photocraft_editor()
    assert memo._known["a.NEF"][0] == float("-inf") and memo._known["b.NEF"][0] == float("-inf")


def test_a_filmstrip_thumb_is_found_without_the_ui_thread_looking_at_the_disk(controller, looks, popout, qapp):
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    asked = []

    class Manager:
        edit_states = controller._window.thumbnail_manager.edit_states

        def get_cached(self, record, size, edit_state=None):
            asked.append((record.path, edit_state))
            return image

    records = [SimpleNamespace(path=f"{n}.NEF", is_folder=False) for n in range(12)]
    controller._window.__dict__.update(_record_at=lambda index: records[index], thumbnail_manager=Manager(), PREVIEW_FILMSTRIP_THUMB_SIZE=None)
    for _ in range(4):
        pixmaps = [controller.preview_filmstrip_thumb(index) for index in range(12)]
    settle(qapp, controller, 12)
    assert len(looks.paths) == 12, "each photo's edits were looked at once, not once per step"
    assert all(thread is not threading.main_thread() for thread in looks.threads)
    assert all(state is not None for _path, state in asked), "the answer is always passed on: the manager is never left to look for itself"
    assert len({id(p) for p in pixmaps}) == 1, "one image, one pixmap"


def test_a_thumbnail_that_is_not_there_yet_is_asked_for_without_the_disk_being_looked_at_either(controller, looks, popout, qapp):
    from unittest.mock import Mock

    requested = []
    manager = Mock()
    manager.get_cached.return_value = None
    manager.request_thumbnail.side_effect = lambda *args, **kwargs: requested.append(kwargs)
    manager.edit_states = controller._window.thumbnail_manager.edit_states
    grid = Mock()
    grid.thumbnail_for.return_value = None
    record = SimpleNamespace(path="a.NEF", is_folder=False)
    controller._window.__dict__.update(
        _record_at=lambda index: record, thumbnail_manager=manager, PREVIEW_FILMSTRIP_THUMB_SIZE=None, grid=grid,
    )
    looks.states["a.NEF"] = (4, 4)
    controller.preview_filmstrip_thumb(0)
    assert requested[0]["edit_state"] == (0, 0)
    assert grid.thumbnail_for.call_args.args == (0, (0, 0))
    settle(qapp, controller, 1)
    controller.preview_filmstrip_thumb(0)
    assert requested[1]["edit_state"] == (4, 4)


def test_the_grids_fallback_thumbnail_uses_the_edit_state_it_is_given_for_the_photo_itself_and_not_for_a_variant():
    from unittest.mock import Mock

    from image_triage.grid import ThumbnailGridView

    photo = SimpleNamespace(path="a.NEF", is_folder=False)
    other = SimpleNamespace(path="a_edit.TIF", is_folder=False)
    manager = Mock()
    grid = SimpleNamespace(
        _items=[photo], _thumbnail_target_size=lambda: "size", _current_variant=lambda item: photo, thumbnail_manager=manager,
    )
    ThumbnailGridView.thumbnail_for(grid, 0, (3, 3))
    assert manager.get_cached.call_args.args == (photo, "size", (3, 3))
    grid._current_variant = lambda item: other  # another file shown for the photo: its edits are not the photo's
    ThumbnailGridView.thumbnail_for(grid, 0, (3, 3))
    assert manager.get_cached.call_args.args == (other, "size", None)
    ThumbnailGridView.thumbnail_for(grid, 0)
    assert manager.get_cached.call_args.args == (other, "size", None)
