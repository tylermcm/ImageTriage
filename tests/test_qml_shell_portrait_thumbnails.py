"""Portrait thumbnails retain the whole photo and do not reuse older cropped cache files."""
import time

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QImage, QPainter

from image_triage.cache import DiskThumbnailCache, ThumbnailKey
from image_triage.qml_shell.thumbs import ThumbService
from test_qml_shell import _load_main, pump, qapp  # noqa: F401


def portrait(path):
    image = QImage(240, 360, QImage.Format_RGB32)
    image.fill(QColor("blue"))
    painter = QPainter(image)
    painter.fillRect(0, 0, 240, 72, QColor("red"))
    painter.fillRect(0, 288, 240, 72, QColor("lime"))
    painter.end()
    assert image.save(str(path))


def test_the_whole_portrait_survives_and_a_cropped_cache_entry_is_not_reused(qapp, tmp_path):
    path = tmp_path / "portrait.png"
    portrait(path)
    disk = DiskThumbnailCache(tmp_path / "cache")
    stat = path.stat()
    legacy_key = ThumbnailKey(path=str(path), modified_ns=stat.st_mtime_ns, file_size=stat.st_size, width=240, height=160)
    cropped = QImage(240, 160, QImage.Format_RGB32)
    cropped.fill(QColor("blue"))
    disk.save(legacy_key, cropped)
    service = ThumbService(disk, workers=1)
    assert service.url(str(path), 240) == "", "a cropped legacy thumbnail must not stand in for the whole portrait"
    assert service.pool.waitForDone(10000)
    thumbnail = QImage(QUrl(service.url(str(path), 240)).toLocalFile())
    assert not thumbnail.isNull()
    assert thumbnail.height() > thumbnail.width()
    assert abs(thumbnail.width() / thumbnail.height() - 2 / 3) < 0.02
    assert thumbnail.pixelColor(thumbnail.width() // 2, 5).red() > 220
    assert thumbnail.pixelColor(thumbnail.width() // 2, thumbnail.height() - 6).green() > 220


def test_the_grid_fits_portraits_and_enables_the_blurred_side_fill(qapp, tmp_path):
    path = tmp_path / "portrait.png"
    portrait(path)
    engine, window, warnings = _load_main(qapp, tmp_path)
    photos = engine.rootContext().contextProperty("photos")
    photos.setRows([dict(path=str(path), name=path.name, ext="PNG", raw=False, decision="", rating=0, ai=50)], str(tmp_path))
    window.setWidth(1440)
    window.setHeight(900)
    thumbnail = None
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        pump(qapp, 0.05)
        pending = [window.contentItem()]
        while pending:
            candidate = pending.pop()
            if "ThumbImage" in candidate.metaObject().className() and candidate.property("path") == str(path) and candidate.property("blurFill") is True:
                thumbnail = candidate
                break
            pending.extend(candidate.childItems())
        if thumbnail is not None and thumbnail.property("letterboxed"):
            break
    assert thumbnail is not None and thumbnail.property("letterboxed") is True
    assert not warnings, warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
