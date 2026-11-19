"""Detail > Super Resolution in the editor, as Camera Raw does it: the photo is upscaled once into a base kept beside it, and every
view and edit after that develops from the base (the RAW preview worker's ``regionRender``). Stand-ins replace the model and the
worker."""
import json
import threading

from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage

from image_triage.qml_shell import raw_super_res
from image_triage.qml_shell.raw_super_res import source_rect, view_rect
from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401


def test_a_view_and_its_part_of_the_photo_as_recorded():
    plain = {"x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6}
    rect = source_rect((0.25, 0.5, 0.5, 1.0), plain)
    assert rect == (0.1 + 0.25 * 0.8, 0.2 + 0.5 * 0.6, 0.1 + 0.5 * 0.8, 0.2 + 0.6)
    for crop in (plain, {**plain, "rotate": 90}, {**plain, "rotate": 270, "flipH": True}, {**plain, "rotate": 180, "flipV": True}):
        back = view_rect(source_rect((0.2, 0.3, 0.6, 0.7), crop), crop)
        assert all(abs(a - b) < 1e-9 for a, b in zip(back, (0.2, 0.3, 0.6, 0.7))), crop
    assert source_rect((0.2, 0.3, 0.6, 0.7), {**plain, "angle": 3.0}) is None        # straightened: not laid over the view


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


def test_the_photo_is_upscaled_once_and_every_edit_develops_from_the_base(shell, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(raw_super_res, "base_path", lambda photo, denoised=False: tmp_path / "superres" / "sample.NEF.superres")
    window, raw, warnings = editing(shell, qapp)
    sr = raw.superResEditor
    gate, made, asked = threading.Event(), [], []

    def fake_make(source, target, cancel, progress):
        made.append(source)
        gate.wait(5)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PCSRBAS1")
        target.with_name(target.name + ".json").write_text(json.dumps({"source": None}), encoding="utf-8")

    def fake_region(request):
        asked.append(request)
        l, t, r, b = request["rect"]
        image = QImage(64, 40, QImage.Format_RGBA8888)
        image.fill(QColor("#336699"))
        return {"ok": True, "shown": [l, t, r, b], "fullSize": [16000, 10000]}, image

    sr.make_base = fake_make
    sr.regions.render = fake_region
    width = raw.fullWidth
    raw.setSuperResolution(True)
    assert until(qapp, lambda: made)
    assert raw.fullWidth == width * 2 and sr.busy and "Upscaling" in sr.message   # made at once, under the AI overlay
    assert window.findChild(QObject, "aiWorkingOverlay").property("title") == "Super Resolution"
    assert "Applied" not in sr.status                                            # nothing is claimed before it is done
    sr.setView(0.40, 0.40, 0.46, 0.46, True)
    pump(qapp, .5)
    assert sr.patches == [] and not asked                                        # nothing developed before the base
    gate.set()
    assert until(qapp, lambda: sr.patches)
    assert not sr.busy and "Applied" in sr.status and len(made) == 1
    assert asked[-1]["op"] == "regionRender" and asked[-1]["path"].endswith("sample.NEF.superres")
    patch = sr.patches[0]
    assert patch["url"].startswith("image://rawpreview/")
    assert patch["l"] <= 0.40 and patch["r"] >= 0.46                            # the view and some of what is around it
    count = len(asked)
    sr.setView(0.405, 0.405, 0.465, 0.465, True)                                 # a small pan: already developed
    pump(qapp, .6)
    assert len(asked) == count
    raw.adjust("exposure", 1.0)                                                  # an edit: developed again from the base,
    assert until(qapp, lambda: len(asked) > count and sr.patches and sr.patches[0]["url"] != patch["url"])
    assert asked[-1]["recipe"]["cameraRaw"]["exposure"] == 1.0 and len(made) == 1  # never upscaled again
    raw.setSuperResolution(False)
    assert until(qapp, lambda: not sr.patches)
    assert raw.fullWidth == width
    assert not [w for w in warnings if "SuperRes" in w or "superRes" in w], warnings


def test_cancel_stops_the_upscale_until_the_photo_changes(shell, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(raw_super_res, "base_path", lambda photo, denoised=False: tmp_path / "superres" / "sample.NEF.superres")
    window, raw, warnings = editing(shell, qapp)
    sr = raw.superResEditor
    made = []

    def fake_make(source, target, cancel, progress):
        made.append(source)
        cancel.wait(5)
        raise InterruptedError("cancelled")

    sr.make_base = fake_make
    raw.setSuperResolution(True)
    assert until(qapp, lambda: made and sr.busy)
    sr.cancel()
    pump(qapp, .8)
    assert not sr.busy and len(made) == 1 and sr.patches == []
    raw.adjust("exposure", 0.5)                                                  # an edit is not a new photo
    pump(qapp, .8)
    assert len(made) == 1
