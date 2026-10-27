"""The native editor receives the preview's order, thumbnails and stable navigation ids."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage, QColor

from image_triage.qml_shell.editor_filmstrip import EditorFilmstrip
from image_triage.qml_shell.photos import PhotoModel
from image_triage.scanner import normalized_path_key
from test_qml_shell import qapp  # noqa: F401


class Host(QObject):
    opened = Signal(str)
    stateChanged = Signal()

    def __init__(self):
        super().__init__()
        self.path, self.state = "/sample/02.jpg", "editing"
        self._proc = SimpleNamespace(control=SimpleNamespace(filmstrip_supported=True))
        self.selected = []

    def openPath(self, path):
        self.selected.append(path)


class Thumbs:
    def __init__(self, cached):
        self.cached, self.requests, self.released = cached, [], []

    def url(self, path, bucket):
        self.requests.append(path)

    def cached_file(self, path, bucket):
        return self.cached

    def unwant(self, path):
        self.released.append(path)


@pytest.fixture
def strip(qapp, tmp_path):
    photos = PhotoModel()
    photos.setRows([{"path": f"/sample/{i:02}.jpg", "name": f"{i:02}.jpg", "ext": "JPG", "rating": i % 5,
                     "decision": "rej" if i % 3 == 0 else "keep"} for i in range(80)], "/sample")
    cached = str(tmp_path / "thumb.jpg")
    image = QImage(40, 30, QImage.Format_RGB32)
    image.fill(QColor("steelblue"))
    assert image.save(cached)
    host, thumbs, preview = Host(), Thumbs(cached), SimpleNamespace(go=lambda row: None)
    adapter = EditorFilmstrip(host, photos, thumbs, preview)
    yield adapter, host, photos, thumbs
    adapter.stop()


def request(adapter, photos, index, serial=1, kind="select", value=None):
    return {"generation": adapter.generation, "request": {"generation": adapter.generation, "serial": serial,
            "index": index, "id": normalized_path_key(photos.row_at(index)["path"]), "kind": kind, "value": value}}


def test_packet_matches_preview_order_filter_selection_and_has_bounded_thumbnails(strip):
    adapter, host, photos, thumbs = strip
    photos.setFilter("keep")
    photos.setSort("rating")
    host.path = photos.row_at(40)["path"]
    adapter.last = 100000
    packet, uploaded = adapter.packet()
    assert packet["total"] == photos.rowCount()
    assert packet["current"] == 40
    assert len(packet["items"]) <= 32
    assert sum("thumbnail" in item for item in packet["items"]) == 1
    for item in packet["items"]:
        assert item["id"] == normalized_path_key(photos.row_at(item["index"])["path"])
        assert len(item["version"]) <= 128
    adapter.packet()
    assert len(thumbs.requests) == len(set(thumbs.requests))


def test_select_and_arrow_requests_use_the_same_photos(strip):
    adapter, host, photos, thumbs = strip
    adapter.apply(host._proc, adapter.generation, request(adapter, photos, 2), None)
    assert host.selected == [photos.row_at(2)["path"]]
    adapter.apply(host._proc, adapter.generation, request(adapter, photos, 2), None)
    assert len(host.selected) == 1
    adapter.apply(host._proc, adapter.generation, request(adapter, photos, 2, 2, "navigate", 12), None)
    assert host.selected[-1] == photos.row_at(12)["path"]


def test_stale_generation_wrong_photo_and_old_process_cannot_navigate(strip):
    adapter, host, photos, thumbs = strip
    old = request(adapter, photos, 2)
    old_generation = adapter.generation
    photos.setSort("rating")
    adapter.apply(host._proc, old_generation, old, None)
    wrong = request(adapter, photos, 2)
    wrong["request"]["id"] = "not-the-photo"
    adapter.apply(host._proc, adapter.generation, wrong, None)
    adapter.apply(object(), adapter.generation, request(adapter, photos, 2), None)
    assert not host.selected


def test_rating_reject_and_thumbnail_window_release(strip):
    adapter, host, photos, thumbs = strip
    adapter.apply(host._proc, adapter.generation, request(adapter, photos, 2, kind="rating", value=5), None)
    assert photos.row_at(2)["rating"] == 5
    adapter.apply(host._proc, adapter.generation, request(adapter, photos, 2, 2, "reject"), None)
    assert photos.row_at(2)["decision"] == "rej"
    adapter.packet()
    adapter.first, adapter.last = 40, 60
    adapter.packet()
    assert thumbs.released
    assert len(adapter.wanted) <= 32


def test_navigation_waits_while_a_photo_is_loading(strip):
    adapter, host, photos, thumbs = strip
    host.state = "starting"
    adapter.apply(host._proc, adapter.generation, request(adapter, photos, 2), None)
    assert not host.selected and adapter.ack == 0
