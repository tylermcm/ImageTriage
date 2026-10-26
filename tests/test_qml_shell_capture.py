"""The inspector's Capture section: the photo's own camera, lens, exposure, capture time and pixel size, and no longer a sample."""
from __future__ import annotations

import time

from PIL import Image
from PIL.TiffImagePlugin import IFDRational
from PySide6.QtCore import QObject

from image_triage.qml_shell import capture
from image_triage.qml_shell.capture import CaptureService
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)


def photo_with_exif(path, size=(600, 400), orientation=1):
    exif = Image.Exif()
    exif[0x010F], exif[0x0110], exif[0x0112] = "NIKON CORPORATION", "NIKON Z 6_2", orientation
    sub = exif.get_ifd(0x8769)
    sub[0x829A], sub[0x829D], sub[0x8827], sub[0x920A] = IFDRational(1, 500), IFDRational(8, 1), 200, IFDRational(35, 1)
    sub[0xA434], sub[0x9003] = "NIKKOR Z 24-70mm f/4 S", "2026:05:14 09:37:12"
    Image.new("RGB", size, (90, 120, 180)).save(path, exif=exif)
    return str(path)


def until(qapp, condition, seconds=6.0):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        pump(qapp, 0.02)
    assert condition()


def test_the_data_is_the_widgets_exif_reading(qapp, tmp_path):
    found = capture.compute(photo_with_exif(tmp_path / "a.jpg"))
    assert found["camera"] == "NIKON CORPORATION NIKON Z 6_2" and found["lens"] == "NIKKOR Z 24-70mm f/4 S"
    assert found["captured"] == "2026-05-14 09:37" and found["focal"] == "35mm"
    assert found["exposure"] == "1/500s \u00b7 f/8 \u00b7 ISO 200"                       # the Widgets' own wording, joined the way the inspector shows it
    assert found["pixels"] == "600 \u00d7 400" and (found["width"], found["height"]) == (600, 400)


def test_a_photo_stored_sideways_is_the_size_it_is_shown(qapp, tmp_path):
    assert capture.compute(photo_with_exif(tmp_path / "turned.jpg", orientation=6))["pixels"] == "400 \u00d7 600"


def test_a_photo_without_exif_still_has_its_size_and_the_rest_blank(qapp, tmp_path):
    Image.new("RGB", (64, 48), (1, 2, 3)).save(tmp_path / "plain.png")
    found = capture.compute(str(tmp_path / "plain.png"))
    assert found["pixels"] == "64 \u00d7 48" and found["camera"] == "" and found["exposure"] == "" and found["captured"] == ""


def test_a_file_that_is_not_a_picture_has_nothing(qapp, tmp_path):
    (tmp_path / "notes.jpg").write_bytes(b"not a picture")
    assert capture.compute(str(tmp_path / "notes.jpg")) is None
    assert capture.compute(str(tmp_path / "missing.jpg")) is None


def test_the_service_reads_a_photo_once_and_says_when(qapp, tmp_path):
    calls = []
    service = CaptureService(loader=lambda p: calls.append(p) or capture.compute(p), parent=qapp)
    path = photo_with_exif(tmp_path / "b.jpg")
    arrived = []
    service.ready.connect(arrived.append)
    assert service.get(path) == {"ready": False}
    service.request(path)
    until(qapp, lambda: path in arrived)
    assert service.get(path)["ready"] and service.get(path)["camera"].startswith("NIKON")
    service.request(path)
    pump(qapp, 0.2)
    assert calls == [path]


def inspector_of(window):
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == "inspector":
            return item
        stack.extend(item.childItems())
    raise AssertionError("no inspector")


def capture_of(inspector):
    value = inspector.property("cap")
    return value.toVariant() if hasattr(value, "toVariant") else value


def test_the_inspector_shows_the_photos_own_capture_data(qapp, tmp_path):
    folder = tmp_path / "shoot"
    folder.mkdir()
    photo_with_exif(folder / "IMG_0001.jpg")
    Image.new("RGB", (32, 24), (5, 5, 5)).save(folder / "IMG_0002.png")
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(900)
    context = engine.rootContext()
    context.contextProperty("captureData")._loader = capture.compute
    context.contextProperty("photos").setRows([{"path": str(folder / n), "name": n, "ext": n.split(".")[1].upper(), "raw": False, "decision": "", "rating": 0, "ai": -1}
                                               for n in ("IMG_0001.jpg", "IMG_0002.png")], str(folder))
    pump(qapp, 0.6)
    inspector = inspector_of(window)
    until(qapp, lambda: capture_of(inspector).get("ready"))
    assert capture_of(inspector)["camera"].startswith("NIKON") and capture_of(inspector)["pixels"] == "600 × 400"
    window.findChild(QObject, "photoGrid").setProperty("currentIndex", 1)                 # the second photo has no EXIF: its size, and nothing else
    until(qapp, lambda: capture_of(inspector).get("pixels") == "32 × 24")
    assert capture_of(inspector)["camera"] == ""
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
