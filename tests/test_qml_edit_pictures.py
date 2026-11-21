"""The editor edits pictures that are not raw too (JPEG, TIFF, PNG, WebP): the same tools on their pixels, as Camera Raw edits a JPEG.
The viewer's button says Edit RAW for a raw and Edit for a picture, so it is plain which is raw editing; tools that need the raw data
(AI Denoise, Demosaic) are for raws only."""
from PySide6.QtCore import QObject

from image_triage.formats import EDITOR_SUFFIXES, RAW_SUFFIXES
from image_triage.qml_shell.raw_editor import RawEditor
from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401


def test_the_editor_takes_raws_and_pictures_but_not_layered_files(qapp):
    assert {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"} <= EDITOR_SUFFIXES and RAW_SUFFIXES <= EDITOR_SUFFIXES
    assert ".psd" not in EDITOR_SUFFIXES
    raw = RawEditor(memory=True)
    raw.open("holiday.JPG")
    assert raw.property("photoPath") == "holiday.JPG" and raw.property("isRaw") is False
    assert "cannot be edited" not in raw.property("error")
    raw.open("holiday.NEF")
    assert raw.property("isRaw") is True


def test_the_viewer_offers_edit_raw_for_a_raw_and_edit_for_a_jpeg(shell, qapp):
    engine, window, photos, popout, warnings = shell
    button = window.findChild(QObject, "viewerEditRawButton")
    rows = [dict(photos.get(i)) for i in range(photos.shown)]
    rows[0].update(ext="NEF", raw=True)                                  # the first photo is a raw, the rest are JPEGs
    photos.setRows(rows, photos.folder_dir()); pump(qapp, .2)
    raw_row, jpeg_row = 0, 1
    popout.open(raw_row); pump(qapp, .3)
    assert button.property("visible") and button.property("text") == "Edit RAW"
    popout.go(jpeg_row); pump(qapp, .3)
    assert button.property("visible") and button.property("text") == "Edit"
    popout.close(); pump(qapp, .2)
