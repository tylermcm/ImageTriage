"""What the grid's cells say about a photo: its name, rating and decision, for a folder read from disk."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject

from image_triage.qml_shell import photos as photos_module
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)


def row(name, ext, **extra):
    return {"path": f"/p/{name}", "name": name, "ext": ext, "raw": False, "decision": "", "rating": 0, "ai": 50, **extra}


def titles(model):
    """name -> title for every shown row (the model sorts by name, so not in the order the rows were given)."""
    return {model.data(model.index(i), photos_module.NameRole): model.data(model.index(i), photos_module.TitleRole) for i in range(model.rowCount())}


def test_the_cell_shows_the_file_name_without_the_format_it_already_tags(qapp):
    model = PhotoModel(parent=qapp)
    model.setRows([row("R.bmp", "BMP"), row("IMG_0001.NEF", "NEF"), row("photo.JPG", "JPG"), row("holiday.final.jpeg", "JPEG"),
                   row("DSC_4201", "NEF"), row("notes", "FILE"), row(".bmp", "BMP")], "/p")
    assert titles(model) == {"R.bmp": "R", "IMG_0001.NEF": "IMG_0001", "photo.JPG": "photo", "holiday.final.jpeg": "holiday.final",
                             "DSC_4201": "DSC_4201", "notes": "notes", ".bmp": ".bmp"}      # only a real trailing extension goes


def test_the_name_role_still_has_the_extension_for_everything_else(qapp):
    model = PhotoModel(parent=qapp)
    model.setRows([row("R.bmp", "BMP")], "/p")
    assert model.data(model.index(0), photos_module.NameRole) == "R.bmp"          # the inspector, menus and toasts name the file in full


def test_a_folder_read_from_disk_starts_unrated_and_undecided(qapp, tmp_path):
    from PIL import Image

    for name in ("a.jpg", "b.png", "c.bmp", "d.jpg"):
        Image.new("RGB", (16, 12), (10, 90, 160)).save(tmp_path / name)
    model = PhotoModel(parent=qapp)
    model.loadFolder(str(tmp_path))
    end = time.monotonic() + 5
    while (model.loading or model.shown == 0) and time.monotonic() < end:
        pump(qapp, 0.02)
    assert model.shown == 4
    assert [model.row_at(i)["rating"] for i in range(4)] == [0, 0, 0, 0]
    assert [model.row_at(i)["decision"] for i in range(4)] == ["", "", "", ""]
    assert model.winCount == 0 and model.keepCount == 0 and model.rejCount == 0
    model.setRating(0, 3)                                                  # a rating the user gives is kept
    assert model.row_at(0)["rating"] == 3


def test_the_window_loads_with_the_title_role(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    model = engine.rootContext().contextProperty("photos")
    model.setRows([row("R.bmp", "BMP"), row("S.png", "PNG")], "/p")
    pump(qapp, 0.4)
    assert window.findChild(QObject, "photoGrid").property("count") == 2
    assert not [w for w in warnings if "Required property" in w or "ReferenceError" in w or "TypeError" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def _made_tone(qapp, thumbs, path, bucket=240):
    """The brightness of the bottom of a photo's thumbnail once the service has made it."""
    assert thumbs.tone(path, bucket) == -1                                # nothing cached yet: unknown, so the card keeps its usual look
    thumbs.url(path, bucket)                                              # asks for it to be made
    end = time.monotonic() + 10
    while thumbs.tone(path, bucket) < 0 and time.monotonic() < end:
        pump(qapp, 0.03)
    return thumbs.tone(path, bucket)


def test_the_card_knows_how_bright_the_bottom_of_a_photo_is(qapp, tmp_path):
    from PIL import Image, ImageDraw

    from image_triage.cache import DiskThumbnailCache
    from image_triage.qml_shell.thumbs import ThumbService

    def photo(name, top, bottom):
        image = Image.new("RGB", (300, 200), top)
        ImageDraw.Draw(image).rectangle([0, 100, 300, 200], fill=bottom)
        path = tmp_path / name
        image.save(path)
        return str(path)

    thumbs = ThumbService(DiskThumbnailCache(tmp_path / "cache"), parent=qapp)
    white = photo("white.png", (255, 255, 255), (255, 255, 255))
    black = photo("black.png", (0, 0, 0), (0, 0, 0))
    bright_bottom = photo("bright_bottom.png", (10, 10, 10), (245, 245, 240))
    dark_bottom = photo("dark_bottom.png", (245, 245, 240), (10, 10, 10))
    assert _made_tone(qapp, thumbs, white) > 0.97
    assert _made_tone(qapp, thumbs, black) < 0.03
    assert _made_tone(qapp, thumbs, bright_bottom) > 0.9                  # only the bottom strip counts, not the top of the photo
    assert _made_tone(qapp, thumbs, dark_bottom) < 0.1
    assert thumbs.tone(str(tmp_path / "missing.png"), 240) == -1          # a photo that is not there
    assert thumbs.tone(white, 240) == thumbs.tone(white, 240)             # the second answer comes from the memory of the first


def test_strip_luma_is_the_brightness_of_the_bottom_of_an_image(qapp):
    from PySide6.QtGui import QColor, QImage, QPainter

    from image_triage.qml_shell.thumbs import strip_luma

    image = QImage(60, 40, QImage.Format.Format_RGB32)
    image.fill(QColor("black"))
    painter = QPainter(image)
    painter.fillRect(0, 20, 60, 20, QColor("white"))                      # the lower half is white
    painter.end()
    assert strip_luma(image) > 0.99
    image.fill(QColor(128, 128, 128))
    assert 0.45 < strip_luma(image) < 0.55
