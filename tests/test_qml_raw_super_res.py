"""Detail > Super Resolution in the editor: the photo counts as twice its size, and while zoomed the part in view is upscaled and laid
over the picture. A stand-in replaces the model."""
import threading

import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage

from image_triage.qml_shell import raw_preview, raw_super_res
from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401


def test_the_piece_is_the_view_and_a_margin_under_the_limit():
    assert raw_super_res.piece((0.4, 0.4, 0.6, 0.6), 1000, 1000) == pytest.approx((0.35, 0.35, 0.65, 0.65))
    l, t, r, b = raw_super_res.piece((0.0, 0.0, 1.0, 1.0), 8000, 5000)                 # the whole 40 MP picture in view
    assert (r - l) * 8000 * (b - t) * 5000 <= raw_super_res.MAX_SOURCE_PIXELS * 1.01
    assert raw_super_res.covers((0.3, 0.3, 0.7, 0.7), (0.4, 0.4, 0.6, 0.6)) and not raw_super_res.covers((0.3, 0.3, 0.5, 0.7), (0.4, 0.4, 0.6, 0.6))


def editing(shell, qapp):
    engine, window, photos, popout, warnings = shell
    popout.open(0)
    raw = popout.rawEditor
    raw.open("sample.NEF")
    image = QImage(800, 500, QImage.Format_RGB32)
    image.fill(QColor("#bf843f"))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [8000, 5000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    pump(qapp, .5)
    return window, raw, warnings


def test_zoomed_in_the_part_in_view_is_upscaled_and_laid_over_the_photo(shell, qapp):
    window, raw, warnings = editing(shell, qapp)
    sr = raw.superResEditor
    gate, calls = threading.Event(), []

    def fake_upscale(image, cancel, progress):
        calls.append((image.width(), image.height()))
        gate.wait(5)
        big = QImage(image.width() * 2, image.height() * 2, QImage.Format_RGB32)
        big.fill(QColor("#ff00ff"))
        return big

    sr.upscale = fake_upscale
    width = raw.fullWidth
    raw.setSuperResolution(True)
    pump(qapp, .2)
    assert raw.fullWidth == width * 2                                       # 100% is now one upscaled pixel per screen pixel
    status = window.findChild(QObject, "rawSuperResolutionStatus")
    detail = QImage(8000, 5000, QImage.Format_RGB32)                         # the full-resolution picture of detail mode
    detail.fill(QColor("#336699"))
    raw._detail_mode, raw._detail = True, raw_preview.put_frame(f"{raw._owner}/test/detail", detail)
    raw.changed.emit()
    sr.setView(0.4, 0.4, 0.5, 0.5, True)
    pump(qapp, .6)
    assert sr.busy and calls and calls[0][0] < 8000                         # a piece, not the whole picture
    overlay = window.findChild(QObject, "aiWorkingOverlay")
    assert overlay.property("title") == "Super Resolution"
    gate.set()
    pump(qapp, .5)
    assert not sr.busy and sr.patch["url"].startswith("image://rawpreview/") and sr.patch["l"] < 0.4 < 0.5 < sr.patch["r"]
    sr.setView(0.42, 0.42, 0.52, 0.52, True)                                 # a small pan inside the piece: nothing new
    pump(qapp, .6)
    assert len(calls) == 1
    raw.setSuperResolution(False)
    pump(qapp, .6)
    assert not sr.patch and raw.fullWidth == width
    assert not [w for w in warnings if "SuperRes" in w or "superRes" in w], warnings
