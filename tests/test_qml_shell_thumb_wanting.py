"""Who wants a thumbnail: a card says so once and takes it back once, so a decode nobody waits for is skipped, and quitting drops the queue."""
from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtQml import QQmlComponent, QQmlEngine

from image_triage.cache import DiskThumbnailCache
from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell import thumbs as thumbs_module
from image_triage.qml_shell.thumbs import ThumbService


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def pump(app, seconds=0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def picture(path):
    image = QImage(400, 300, QImage.Format.Format_RGB32)
    image.fill(QColor("teal"))
    assert image.save(str(path), "JPEG")
    return str(path)


def test_asking_for_the_url_again_does_not_add_to_the_count(qapp, tmp_path):
    photo = picture(tmp_path / "a.jpg")
    service = ThumbService(DiskThumbnailCache(tmp_path / "cache"), workers=1)
    service.pool.setMaxThreadCount(0)                        # nothing is decoded: only the bookkeeping is looked at
    service.want(photo)                                      # one card shows the photo...
    for bucket in (240, 240, 360, 240, 540):                 # ...and asks for its URL as often as its size and `ready` make it
        service.url(photo, bucket)
    assert service._wanted == {photo: 1}
    service.want(photo)                                      # a second card (the viewer's strip) shows it too
    service.unwant(photo)
    assert service._wanted == {photo: 1}                     # still wanted by the first
    service.unwant(photo)
    assert service._wanted == {}                             # the one card that showed it has gone: queued decodes of it are skipped
    service.shutdown(0)


def test_a_decode_whose_card_has_gone_is_skipped(qapp, tmp_path, monkeypatch):
    first, second = picture(tmp_path / "first.jpg"), picture(tmp_path / "second.jpg")
    cache = DiskThumbnailCache(tmp_path / "cache")
    service = ThumbService(cache, workers=1)
    release, decoded = threading.Event(), []
    real = thumbs_module.load_image_for_display

    def load(path, *args, **kwargs):
        decoded.append(path)
        release.wait(5)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(thumbs_module, "load_image_for_display", load)
    for photo in (first, second):
        service.want(photo)
        for _again in range(4):
            service.url(photo, 240)
    service.unwant(second)                                   # its card was recycled while the single worker was busy with the first
    release.set()
    assert service.pool.waitForDone(10000)
    assert decoded == [first]


def test_a_card_tells_the_service_once_per_photo(qapp, tmp_path):
    class Recorder(QObject):
        ready = Signal(str)

        def __init__(self):
            super().__init__()
            self.wanted, self.released, self.urls = [], [], 0

        @Slot(str)
        def want(self, path):
            self.wanted.append(path)

        @Slot(str)
        def unwant(self, path):
            self.released.append(path)

        @Slot(str, int, result=str)
        def url(self, path, bucket):
            self.urls += 1
            return ""

        @Slot()
        def notePainted(self):
            pass

    recorder = Recorder()
    engine = QQmlEngine()
    engine.rootContext().setContextProperty("thumbs", recorder)
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(shell_app.QML_DIR / "ThumbImage.qml")))
    card = component.createWithInitialProperties({"path": "/photos/a.jpg", "width": 120, "height": 80})
    assert card is not None, component.errorString()
    pump(qapp)
    for bucket in (360, 540, 240):
        card.setProperty("bucket", bucket)
    for _signal in range(3):
        recorder.ready.emit("/photos/a.jpg")
    pump(qapp)
    assert recorder.urls >= 6 and recorder.wanted == ["/photos/a.jpg"] and recorder.released == []
    card.setProperty("path", "/photos/b.jpg")                # the card is recycled for another photo
    pump(qapp)
    assert recorder.wanted == ["/photos/a.jpg", "/photos/b.jpg"] and recorder.released == ["/photos/a.jpg"]
    card.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    pump(qapp, 0.2)
    assert recorder.released == ["/photos/a.jpg", "/photos/b.jpg"]
    engine.deleteLater()
    pump(qapp)


def test_quitting_drops_the_queued_thumbnails(qapp, tmp_path, monkeypatch):
    photos = [picture(tmp_path / f"p{index}.jpg") for index in range(12)]
    service = ThumbService(DiskThumbnailCache(tmp_path / "cache"), workers=1)
    release, decoded = threading.Event(), []
    real = thumbs_module.load_image_for_display

    def load(path, *args, **kwargs):
        decoded.append(path)
        release.wait(5)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(thumbs_module, "load_image_for_display", load)
    for photo in photos:
        service.want(photo)
        service.url(photo, 240)
    deadline = time.monotonic() + 5
    while not decoded and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    service.shutdown(200)                                    # the wait for the one decode in flight is bounded
    assert time.monotonic() - started < 2.0
    release.set()
    assert service.pool.waitForDone(10000)
    assert len(decoded) == 1                                 # the other eleven were never started
    assert service.url(photos[5], 240) == "" and service.pool.activeThreadCount() == 0      # and nothing new is queued
