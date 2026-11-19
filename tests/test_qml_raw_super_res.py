"""Detail > Super Resolution in the editor: the photo counts as twice its size, and while zoomed the photo is upscaled in tiles laid
over the picture: the ones in view first, then a ring around them; tiles are kept, so panning back costs nothing. A stand-in replaces
the model."""
import threading

from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage

from image_triage.qml_shell import raw_preview, raw_super_res
from image_triage.qml_shell.raw_super_res import TILE
from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401


def test_the_tiles_of_a_view_and_the_ring_around_it():
    w, h = 8 * TILE, 4 * TILE
    assert raw_super_res.tiles_for((0.0, 0.0, 0.25, 0.5), w, h) == [(0, 0), (1, 0), (0, 1), (1, 1)]
    ring = raw_super_res.tiles_for((0.0, 0.0, 0.25, 0.5), w, h, grow=1)
    assert (2, 2) in ring and len(ring) == 9                                 # clipped at the picture's edge
    assert raw_super_res.nearest([(0, 0), (5, 3), (1, 1)], (0.0, 0.0, 0.30, 0.6), w, h, limit=2) == [(1, 1), (0, 0)]


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


def test_zoomed_in_the_photo_is_upscaled_in_tiles_that_are_kept(shell, qapp):
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
    assert "Zoom in" in sr.status and "now" not in sr.status                # nothing is claimed before it is done
    raw._detail_mode = True
    sr.setView(0.40, 0.40, 0.46, 0.46, True)
    pump(qapp, .5)
    assert sr.busy and "full-resolution picture" in sr.message              # the wait for the picture is shown as work
    assert window.findChild(QObject, "aiWorkingOverlay").property("title") == "Super Resolution"
    detail = QImage(8000, 5000, QImage.Format_RGB32)                        # the full-resolution picture of detail mode
    detail.fill(QColor("#336699"))
    raw._detail = raw_preview.put_frame(f"{raw._owner}/test/detail", detail)
    raw.changed.emit()
    pump(qapp, .5)
    assert sr.busy and len(calls) == 1 and calls[0][0] < 8000                # the tiles in view, not the whole picture
    gate.set()
    for _ in range(40):                                                      # the view, then the ring around it (silently)
        pump(qapp, .1)
        if len(calls) >= 2 and not sr.busy:
            break
    assert not sr.busy and len(sr.patches) > 0 and all(p["url"].startswith("image://rawpreview/srtile/") for p in sr.patches)
    assert len(calls) == 2, calls
    done = len(calls)
    sr.setView(0.41, 0.41, 0.47, 0.47, True)                                 # a small pan: inside what is already upscaled
    pump(qapp, .6)
    assert len(calls) == done and not sr.busy
    sr.setView(0.80, 0.80, 0.86, 0.86, True)                                 # far away: new tiles
    pump(qapp, 1.0)
    for _ in range(40):
        pump(qapp, .1)
        if not sr.busy and len(calls) >= done + 2:
            break
    farther = len(calls)
    sr.setView(0.40, 0.40, 0.46, 0.46, True)                                 # back again: the tiles were kept
    pump(qapp, .6)
    assert len(calls) == farther and not sr.busy and sr.patches
    raw.setSuperResolution(False)
    pump(qapp, .6)
    assert not sr.patches and raw.fullWidth == width
    assert not [w for w in warnings if "SuperRes" in w or "superRes" in w], warnings
