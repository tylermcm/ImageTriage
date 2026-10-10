"""The pieces that keep browsing inside the speed budget: preload order, dropping decodes for photos
already passed, and not restyling widgets whose style has not changed."""
from __future__ import annotations

from queue import SimpleQueue

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from image_triage.preload_order import candidate_indexes
from image_triage.preview import PreviewRequest, PreviewTask, _set_style_sheet


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def order(index, total, travel=0, limit=None):
    result = list(candidate_indexes(index, total, travel))
    return result if limit is None else result[:limit]


def test_with_no_direction_neighbours_alternate_evenly():
    assert order(5, 20, 0, 7) == [5, 6, 4, 7, 3, 8, 2]


def test_travel_prefetches_two_ahead_for_each_one_behind():
    assert order(10, 40, 1, 7) == [10, 11, 12, 9, 13, 14, 8]
    assert order(10, 40, -1, 7) == [10, 9, 8, 11, 7, 6, 12]


def test_edges_and_tiny_folders_yield_each_photo_once():
    assert order(0, 5, 1) == [0, 1, 2, 3, 4]
    assert order(4, 5, 1) == [4, 3, 2, 1, 0]
    assert sorted(order(2, 5, -1)) == [0, 1, 2, 3, 4]
    assert order(0, 1, 1) == [0]
    assert order(0, 0, 1) == []


@pytest.mark.parametrize("travel", [-1, 0, 1])
def test_every_photo_is_reachable_exactly_once(travel):
    result = order(37, 100, travel)
    assert sorted(result) == list(range(100))


def request(path, token=1, *, cache_only=False):
    return PreviewRequest(path=path, token=token, slot=0, apply_saved_edits=False, target_size=QSize(64, 64),
                          source_signature=None, prefer_embedded=True, cache_only=cache_only)


def test_a_decode_for_a_photo_already_passed_is_dropped_before_it_starts(qapp, tmp_path, monkeypatch):
    path = tmp_path / "a.jpg"
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    image.save(str(path), "JPEG")
    loads = []
    monkeypatch.setattr("image_triage.preview.load_image_for_display", lambda *a, **k: loads.append(a) or (image, None))
    queue: SimpleQueue = SimpleQueue()
    PreviewTask(request(str(path)), queue, is_current=lambda: False).run()
    state, _request, reason, _at = queue.get_nowait()
    assert (state, reason) == ("failed", "superseded")
    assert loads == [] and queue.empty()


def test_a_decode_for_the_photo_on_screen_still_runs(qapp, tmp_path, monkeypatch):
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    monkeypatch.setattr("image_triage.preview.load_image_for_display", lambda *a, **k: (image, None))
    queue: SimpleQueue = SimpleQueue()
    PreviewTask(request(str(tmp_path / "a.jpg")), queue, is_current=lambda: True).run()
    assert queue.get_nowait()[0] == "ready"


def test_tasks_without_a_currency_check_behave_as_before(qapp, tmp_path, monkeypatch):
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    monkeypatch.setattr("image_triage.preview.load_image_for_display", lambda *a, **k: (image, None))
    queue: SimpleQueue = SimpleQueue()
    PreviewTask(request(str(tmp_path / "a.jpg"), cache_only=True), queue).run()
    assert queue.get_nowait()[0] == "ready"


def test_an_unchanged_style_sheet_is_not_reapplied(qapp):
    calls = []

    class Widget(QWidget):
        def setStyleSheet(self, css):  # noqa: N802 - Qt's name
            calls.append(css)
            super().setStyleSheet(css)

    widget = Widget()
    _set_style_sheet(widget, "QWidget { color: red; }")
    _set_style_sheet(widget, "QWidget { color: red; }")
    _set_style_sheet(widget, "QWidget { color: blue; }")
    assert calls == ["QWidget { color: red; }", "QWidget { color: blue; }"]
