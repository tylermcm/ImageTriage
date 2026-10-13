"""``EditStateMemo``: the photos' edit states, remembered, so that the UI thread stops asking the disk about every visible photo on every
repaint (the grid did it twice per cell per paint, the filmstrip for a dozen photos per step: more than half of the thread's time while
flipping through photos), and what the grid and the thumbnail manager do with it."""
from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from image_triage import cache as cache_module
from image_triage.cache import EditStateMemo
from image_triage.models import ImageRecord
from image_triage.thumbnails import ThumbnailManager


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class Looks:
    """Stands in for ``edit_state_for``: counts the lookups and the threads that made them, and can hold a lookup until released."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.threads: list[threading.Thread] = []
        self.states: dict[str, tuple[int, int]] = {}
        self.default: tuple[int, int] = (0, 0)
        self.gate: threading.Event | None = None
        self.error: Exception | None = None

    def __call__(self, path):
        self.threads.append(threading.current_thread())
        self.paths.append(str(path))
        if self.gate is not None:
            assert self.gate.wait(5), "a held lookup was never released"
        if self.error is not None:
            raise self.error
        return self.states.get(str(path), self.default)


def wait_for(condition, qapp=None, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if qapp is not None:
            qapp.processEvents()
        if condition():
            return
        time.sleep(0.002)
    raise AssertionError("the condition was not met in time")


@pytest.fixture
def looks():
    return Looks()


@pytest.fixture
def changed():
    return []


@pytest.fixture
def memo(looks, changed):
    return EditStateMemo(probe=looks, on_changed=changed.append)


def settled(memo: EditStateMemo) -> bool:
    return not memo._pending


# --- exact: the first answer is found on the asking thread, the rest are remembered ------------------------

def test_the_first_answer_is_found_on_the_asking_thread_and_then_remembered(memo, looks):
    looks.states["a.NEF"] = (5, 6)
    assert memo.exact("a.NEF") == (5, 6)
    for _ in range(20):
        assert memo.exact("a.NEF") == (5, 6)
    assert looks.paths == ["a.NEF"]
    assert looks.threads == [threading.current_thread()]


def test_a_path_object_and_its_string_are_the_same_photo(memo, looks, tmp_path):
    path = tmp_path / "a.NEF"
    memo.exact(path)
    memo.exact(str(path))
    assert len(looks.paths) == 1


def test_an_answer_that_has_got_old_is_given_again_at_once_and_looked_for_again_on_another_thread(memo, looks, changed, monkeypatch):
    looks.states["a.NEF"] = (1, 1)
    memo.exact("a.NEF")
    monkeypatch.setattr(EditStateMemo, "TTL_S", 0.0)
    looks.states["a.NEF"] = (2, 2)
    assert memo.exact("a.NEF") == (1, 1), "the old answer, without waiting for the disk"
    wait_for(lambda: len(looks.paths) == 2 and settled(memo))
    assert looks.threads[1] is not threading.current_thread()
    assert changed == ["a.NEF"], "and it is said that the answer was different"
    monkeypatch.setattr(EditStateMemo, "TTL_S", 10.0)
    assert memo.exact("a.NEF") == (2, 2)


def test_an_answer_that_turns_out_the_same_is_not_reported_as_a_change(memo, looks, changed, monkeypatch):
    looks.states["a.NEF"] = (1, 1)
    memo.exact("a.NEF")
    monkeypatch.setattr(EditStateMemo, "TTL_S", 0.0)
    memo.exact("a.NEF")
    wait_for(lambda: len(looks.paths) == 2 and settled(memo))
    assert changed == []


# --- peek: never the disk on the asking thread ---------------------------------------------------------

def test_peek_says_no_edits_until_the_first_answer_has_been_found_elsewhere(memo, looks, changed):
    looks.states["a.NEF"] = (5, 6)
    assert memo.peek("a.NEF") == (0, 0)
    wait_for(lambda: bool(looks.paths) and settled(memo))
    assert looks.threads[0] is not threading.current_thread()
    assert memo.peek("a.NEF") == (5, 6)
    assert changed == ["a.NEF"], "the first answer was not what was assumed"


def test_a_photo_with_no_edits_is_never_reported_as_changed(memo, looks, changed):
    memo.peek("a.NEF")
    wait_for(lambda: bool(looks.paths) and settled(memo))
    assert changed == []


def test_asking_again_before_the_answer_arrives_does_not_look_again(memo, looks):
    looks.gate = threading.Event()
    for _ in range(10):
        memo.peek("a.NEF")
    wait_for(lambda: bool(looks.threads))
    for _ in range(10):
        memo.peek("a.NEF")
    looks.gate.set()
    wait_for(lambda: settled(memo))
    assert looks.paths == ["a.NEF"]


def test_the_lookup_thread_never_keeps_the_program_from_closing(memo, looks):
    memo.peek("a.NEF")
    wait_for(lambda: bool(looks.paths))
    assert memo._thread.daemon


def test_one_thread_looks_for_every_photo(memo, looks):
    for n in range(30):
        memo.peek(f"{n}.NEF")
    wait_for(lambda: len(looks.paths) == 30 and settled(memo))
    assert len({id(thread) for thread in looks.threads}) == 1


# --- stale ----------------------------------------------------------------------------------------------

def test_stale_makes_the_next_ask_look_again_and_meanwhile_gives_the_old_answer(memo, looks, changed):
    looks.states["a.NEF"] = (1, 1)
    looks.states["b.NEF"] = (3, 3)
    memo.exact("a.NEF")
    memo.exact("b.NEF")
    looks.states["a.NEF"] = (9, 9)
    memo.stale("a.NEF")
    assert memo.exact("b.NEF") == (3, 3)
    assert memo.exact("a.NEF") == (1, 1)
    wait_for(lambda: len(looks.paths) == 3 and settled(memo))
    assert looks.paths == ["a.NEF", "b.NEF", "a.NEF"], "only the photo that may have changed"
    assert memo.exact("a.NEF") == (9, 9)
    assert changed == ["a.NEF"]


def test_stale_without_a_path_is_for_every_photo(memo, looks):
    for name in ("a.NEF", "b.NEF"):
        memo.exact(name)
    memo.stale()
    memo.exact("a.NEF")
    memo.exact("b.NEF")
    wait_for(lambda: len(looks.paths) == 4 and settled(memo))


def test_a_photo_nobody_has_asked_about_is_not_made_up_by_stale(memo, looks):
    memo.stale("never.NEF")
    memo.stale()
    assert looks.paths == []
    memo.exact("never.NEF")
    assert looks.paths == ["never.NEF"]


# --- robustness ---------------------------------------------------------------------------------------------

def test_a_lookup_that_fails_is_no_edits_and_the_thread_goes_on(memo, looks, changed):
    looks.error = RuntimeError("the drive went away")
    assert memo.exact("a.NEF") == (0, 0)
    memo.peek("b.NEF")
    wait_for(lambda: len(looks.paths) == 2 and settled(memo))
    looks.error = None
    looks.states["c.NEF"] = (4, 4)
    memo.peek("c.NEF")
    wait_for(lambda: len(looks.paths) == 3 and settled(memo))
    assert memo.peek("c.NEF") == (4, 4)


def test_the_memo_is_bounded_and_forgets_the_oldest_first(memo, looks, monkeypatch):
    monkeypatch.setattr(EditStateMemo, "MAX_ENTRIES", 8)
    for n in range(12):
        memo.exact(f"{n}.NEF")
    assert len(memo._known) == 8
    assert "0.NEF" not in memo._known and "11.NEF" in memo._known


def test_a_receiver_that_has_gone_ends_the_thread_quietly(looks):
    def gone(_path):
        raise RuntimeError("Internal C++ object already deleted")

    memo = EditStateMemo(probe=looks, on_changed=gone)
    looks.states["a.NEF"] = (1, 1)
    memo.peek("a.NEF")
    wait_for(lambda: not memo._thread.is_alive())


def test_it_uses_edit_state_for_unless_given_something_else(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cache_module, "edit_state_for", lambda path: calls.append(str(path)) or (7, 7))
    assert EditStateMemo().exact("a.NEF") == (7, 7)
    assert calls == ["a.NEF"]


# --- the thumbnail manager ---------------------------------------------------------------------------------

def record(name: str = "a.NEF") -> ImageRecord:
    return ImageRecord(path=name, name=name, size=2, modified_ns=1)


def test_keys_come_from_the_memo_and_do_not_look_at_the_disk_again(qapp, looks):
    manager = ThumbnailManager()
    manager.edit_states._probe = looks
    looks.states["a.NEF"] = (5, 6)
    keys = [manager.make_key(record(), QSize(8, 8)) for _ in range(10)]
    assert {key.edit_state for key in keys} == {(5, 6)}
    assert looks.paths == ["a.NEF"]
    manager._drain_timer.stop()


def test_an_edit_state_that_is_given_is_used_and_nothing_is_looked_up(qapp, looks):
    manager = ThumbnailManager()
    manager.edit_states._probe = looks
    assert manager.make_key(record(), QSize(8, 8), (5, 6)).edit_state == (5, 6)
    assert manager.make_key(record(), QSize(8, 8), (0, 0)).edit_state == (0, 0), "(0, 0) is an answer, not 'not given'"
    manager.get_cached(record(), QSize(8, 8), (7, 7))
    manager.pool.start = lambda *args, **kwargs: None
    manager.request_thumbnail(record(), QSize(8, 8), edit_state=(8, 8))
    assert looks.paths == []
    assert any(key.edit_state == (8, 8) for key in manager._pending)
    manager._drain_timer.stop()


def test_a_photo_whose_edits_were_not_what_was_known_is_announced_on_the_ui_thread(qapp, looks):
    manager = ThumbnailManager()
    manager.edit_states._probe = looks
    heard: list[tuple[str, threading.Thread]] = []
    manager.edit_state_changed.connect(lambda path: heard.append((path, threading.current_thread())))
    looks.states["a.NEF"] = (5, 6)
    manager.edit_states.peek("a.NEF")
    wait_for(lambda: bool(heard), qapp)
    assert heard == [("a.NEF", threading.main_thread())]
    manager._drain_timer.stop()


# --- the grid -------------------------------------------------------------------------------------------------

@pytest.fixture
def grid(qapp, looks):
    from image_triage.grid import ThumbnailGridView

    manager = ThumbnailManager()
    manager.edit_states._probe = looks
    widget = ThumbnailGridView(manager)
    widget.resize(900, 700)
    records = [ImageRecord(path=f"C:/photos/IMG_{n:03d}.NEF", name=f"IMG_{n:03d}.NEF", size=n + 1, modified_ns=n + 1) for n in range(30)]
    widget.set_items(records) if hasattr(widget, "set_items") else None
    if not widget._items:
        widget._items = records
        widget._visible_item_indexes = list(range(len(records)))
    widget.show()
    qapp.processEvents()
    yield widget
    manager._drain_timer.stop()
    widget.deleteLater()
    qapp.processEvents()


def test_repainting_the_grid_does_not_look_at_the_disk_again(grid, looks, qapp):
    grid.grab()
    first = len(looks.paths)
    assert first > 0, "the first paint found the visible photos' edit states"
    assert len(set(looks.paths)) == first, "each photo once, not once for the key and once for the lookup"
    for _ in range(5):
        grid.viewport().update()
        grid.grab()
    assert len(looks.paths) == first


def test_requesting_the_visible_thumbnails_passes_the_edit_state_on(grid, looks, qapp):
    grid.grab()
    before = len(looks.paths)
    seen = []
    grid.thumbnail_manager.request_thumbnail = lambda record, size, priority=0, **kwargs: seen.append(kwargs)
    grid._request_visible_thumbnails()
    assert seen, "something was requested"
    assert all("edit_state" in kwargs for kwargs in seen)
    assert len(looks.paths) == before, "the keys and the requests used what was known"


def test_a_change_of_edit_state_clears_the_photo_s_pixmap_and_asks_again(grid, looks, qapp):
    path = grid._items[0].path
    grid._variant_path_to_index[path] = 0
    grid._clear_pixmap_cache = Mock()
    grid._schedule_visible_thumbnail_requests = Mock()
    grid.thumbnail_manager.edit_state_changed.emit(path)
    qapp.processEvents()
    grid._clear_pixmap_cache.assert_called_once_with(paths=(path,))
    grid._schedule_visible_thumbnail_requests.assert_called_once()
    grid._clear_pixmap_cache.reset_mock()
    grid.thumbnail_manager.edit_state_changed.emit("C:/not/in/this/folder.NEF")
    qapp.processEvents()
    grid._clear_pixmap_cache.assert_not_called()


def test_changed_items_make_the_memo_look_at_their_edits_again(grid, looks, qapp):
    from image_triage.grid import GridDeltaUpdate

    path = grid._items[3].path
    grid.thumbnail_manager.edit_states.exact(path)
    looks.states[path] = (9, 9)
    grid.update_items(GridDeltaUpdate(changed_paths=(path,), preserve_pixmap_cache=False))
    wait_for(lambda: grid.thumbnail_manager.edit_states.exact(path) == (9, 9) and settled(grid.thumbnail_manager.edit_states), qapp)
