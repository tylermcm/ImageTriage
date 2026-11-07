"""The pieces that keep browsing inside the speed budget: preload order, dropping decodes for photos
already passed, and not restyling widgets whose style has not changed."""
from __future__ import annotations

from queue import SimpleQueue

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from image_triage.preload_order import candidate_indexes


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
