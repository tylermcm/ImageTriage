"""The popout in the QML shell: the Widgets app's full-screen viewer opened from the grid, stepping, deciding and closing."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest

from image_triage.cache import DiskThumbnailCache
from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.thumbs import ThumbService
from qml_support import NoThumbs
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)

COUNT = 6


@pytest.fixture
def photos(qapp, tmp_path):
    from PIL import Image

    model = PhotoModel(parent=qapp)
    items = []
    for index in range(COUNT):
        path = tmp_path / f"DSC_43{index:02d}.jpg"
        Image.new("RGB", (96, 64), (40 * index, 90, 160)).save(path)
        items.append({"path": str(path), "name": f"DSC_43{index:02d}", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 60})
    model.setRows(items, str(tmp_path))
    return model




def path_of(photos, row):
    return photos.row_at(row)["path"]
























def test_the_window_opens_the_viewer_and_takes_the_row_back(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    popout = engine.rootContext().contextProperty("popout")
    photos = engine.rootContext().contextProperty("photos")
    photos.setRows(rows(12), "/p")
    pump(qapp, 0.5)
    grid = window.findChild(QObject, "photoGrid")
    grid.setProperty("currentIndex", 2)
    popout.open(2)                                                # what Enter, Space and a double click send
    assert popout.property("isOpen")
    popout.step(3)
    popout.close()
    pump(qapp, 0.3)
    assert grid.property("currentIndex") == 5                     # the grid is on the photo the viewer ended on
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "is not a type" in w], warnings
    popout.shutdown()
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
