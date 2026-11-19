"""Detail > Super Resolution in the editor: the photo counts as twice its size, and once the full-resolution picture is there the whole
photo is upscaled in one run and shown all at once (as tiles the viewer loads from disk). A stand-in replaces the model."""
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QColor, QImage

from image_triage.qml_shell import raw_preview, raw_super_res
from image_triage.qml_shell.raw_super_res import TILE
from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401


def test_the_tiles_of_a_view():
    w, h = 8 * TILE, 4 * TILE
    assert raw_super_res.tiles_for((0.0, 0.0, 0.25, 0.5), w, h) == [(0, 0), (1, 0), (0, 1), (1, 1)]
    assert len(raw_super_res.tiles_for((0.0, 0.0, 0.25, 0.5), w, h, grow=1)) == 9          # clipped at the photo's edge


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


def until(qapp, condition, seconds=5.0):
    import time

    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        pump(qapp, 0.05)
    return condition()


def test_the_whole_photo_is_upscaled_in_one_run_and_shown_at_once(shell, qapp):
    window, raw, warnings = editing(shell, qapp)
    sr = raw.superResEditor
    gate, runs = threading.Event(), []

    def fake_upscale(image, folder, cancel, progress):
        runs.append((image.width(), image.height()))
        gate.wait(5)
        folder.mkdir(parents=True, exist_ok=True)
        w, h = image.width() * 2, image.height() * 2
        for iy in range(-(-h // TILE)):
            for ix in range(-(-w // TILE)):
                (folder / f"{ix}_{iy}.jpg").write_bytes(b"jpeg")
        return w, h

    sr.upscale = fake_upscale
    width = raw.fullWidth
    raw.setSuperResolution(True)
    pump(qapp, .2)
    assert raw.fullWidth == width * 2 and "Zoom in" in sr.status                # nothing is claimed before it is done
    raw._detail_mode = True
    sr.setView(0.40, 0.40, 0.46, 0.46, True)
    pump(qapp, .5)
    assert sr.busy and "full-resolution picture" in sr.message                  # the wait for the picture is shown as work
    assert window.findChild(QObject, "aiWorkingOverlay").property("title") == "Super Resolution"
    detail = QImage(3000, 2000, QImage.Format_RGB32)                            # the full-resolution picture of detail mode
    detail.fill(QColor("#336699"))
    raw._detail = raw_preview.put_frame(f"{raw._owner}/test/detail", detail)
    raw.changed.emit()
    assert until(qapp, lambda: runs)
    assert runs == [(3000, 2000)] and sr.busy and "whole photo" in sr.message   # the whole photo, in one run
    assert sr.patches == []                                                     # nothing shown until all of it is done
    gate.set()
    assert until(qapp, lambda: not sr.busy and sr.patches)
    folder = Path(QUrl(sr.patches[0]["url"]).toLocalFile()).parent
    assert len(list(folder.glob("*.jpg"))) == 6 * 4                            # every tile of the 6000 x 4000 photo
    assert "whole photo" in sr.status
    sr.setView(0.85, 0.85, 0.95, 0.95, True)                                    # anywhere else: already done
    pump(qapp, .6)
    assert len(runs) == 1 and not sr.busy and sr.patches
    raw.setSuperResolution(False)
    assert until(qapp, lambda: not sr.patches)
    assert not folder.exists() and raw.fullWidth == width                      # its tiles are deleted
    assert not [w for w in warnings if "SuperRes" in w or "superRes" in w], warnings
