"""The quick viewer: a photo opened from the file manager shows at once in a window of its own, the folder's other photos follow, and
Library hands the photo to the full app."""
from __future__ import annotations

import os
import struct
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QIODevice, QObject
from PySide6.QtGui import QColor, QGuiApplication, QImage

from image_triage import quick_view
from image_triage.quick_view import decode


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app, condition, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(app, 0.03)
        if condition():
            return True
    return False


def jpeg(path: Path, width: int, height: int, color: str = "#336699", orientation: int = 0) -> Path:
    from PIL import Image

    image = Image.new("RGB", (width, height), color)
    if orientation:
        exif = image.getexif()
        exif[0x0112] = orientation
        image.save(path, "JPEG", exif=exif)
    else:
        image.save(path, "JPEG")
    return path


def fake_raw(path: Path, width: int = 1200, height: int = 800) -> Path:
    """A TIFF-shaped RAW holding a JPEG preview (what the camera embeds), as tests/test_raw_embedded_jpeg.py builds it."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#cc5522"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG")
    buffer.close()
    payload = bytes(data)
    offset = 8 + 2 + 2 * 12 + 4
    path.write_bytes(b"".join((b"II", struct.pack("<H", 42), struct.pack("<I", 8), struct.pack("<H", 2),
                               struct.pack("<HHI", 0x0201, 4, 1), struct.pack("<I", offset),
                               struct.pack("<HHI", 0x0202, 4, 1), struct.pack("<I", len(payload)), struct.pack("<I", 0), payload)))
    return path


# ---- decoding -------------------------------------------------------------------------------------------------------------------------
def test_a_jpeg_is_read_at_screen_size_and_its_own_size_is_kept(qapp, tmp_path):
    picture = decode.load(str(jpeg(tmp_path / "a.jpg", 4000, 3000)), 1000)
    assert (picture.image.width(), picture.image.height()) == (1000, 750)
    assert (picture.width, picture.height, picture.kind) == (4000, 3000, "file")
    whole = decode.load(str(tmp_path / "a.jpg"), 0)
    assert whole.image.width() == 4000


def test_a_photo_taken_with_the_camera_turned_is_shown_upright(qapp, tmp_path):
    picture = decode.load(str(jpeg(tmp_path / "turned.jpg", 600, 400, orientation=6)), 300)
    assert (picture.image.width(), picture.image.height()) == (200, 300)
    assert (picture.width, picture.height) == (400, 600)


def test_a_raw_shows_the_preview_the_camera_embedded(qapp, tmp_path):
    picture = decode.load(str(fake_raw(tmp_path / "frame.NEF")), 600)
    assert picture.kind == "embedded" and picture.image.width() == 600
    assert (picture.width, picture.height) == (1200, 800)
    assert picture.image.pixelColor(10, 10).red() > 150


def test_saved_edits_are_shown_but_only_looked_for_beside_an_edits_folder(qapp, tmp_path, monkeypatch):
    from image_triage import photocraft_bridge

    photo = jpeg(tmp_path / "edited.jpg", 800, 600, "#0000ff")
    render = jpeg(tmp_path / "render.jpg", 800, 600, "#ff0000")
    asked = []
    monkeypatch.setattr(photocraft_bridge, "saved_render_path", lambda path: asked.append(path) or render)
    assert decode.load(str(photo), 400).kind == "file" and asked == [], "no edits folder: the bridge is not even asked"
    (tmp_path / decode.EDIT_ROOT_NAME).mkdir()
    picture = decode.load(str(photo), 400)
    assert picture.kind == "edited" and picture.image.pixelColor(5, 5).red() > 200


def test_a_missing_or_broken_photo_says_so(qapp, tmp_path):
    assert decode.load(str(tmp_path / "gone.jpg"), 400).error
    (tmp_path / "broken.jpg").write_bytes(b"not a picture")
    assert decode.load(str(tmp_path / "broken.jpg"), 400).image.isNull()


def test_the_folder_is_in_name_order_with_one_entry_per_shot(tmp_path):
    for name in ("IMG_10.jpg", "IMG_2.jpg", "IMG_2.NEF", "IMG_3.NEF", "IMG_3.JPG", "notes.txt", ".hidden.jpg"):
        (tmp_path / name).write_bytes(b"x")
    names = [Path(p).name for p in decode.folder_photos(str(tmp_path / "IMG_2.jpg"))]
    assert names == ["IMG_2.jpg", "IMG_3.JPG", "IMG_10.jpg"], "numbers count as numbers; a RAW's JPEG stands for the pair"
    opened_raw = [Path(p).name for p in decode.folder_photos(str(tmp_path / "IMG_3.NEF"))]
    assert opened_raw == ["IMG_2.jpg", "IMG_3.NEF", "IMG_10.jpg"], "the file that was opened stays"


# ---- when the viewer is used --------------------------------------------------------------------------------------------------------
def test_the_setting_and_the_environment_switch_the_viewer(monkeypatch):
    from image_triage import app_identity

    class Settings:
        def __init__(self, value):
            self.v = value

        def value(self, _key, default):
            return default if self.v is None else self.v

    monkeypatch.delenv(quick_view.ENV_SWITCH, raising=False)
    monkeypatch.setattr(app_identity, "user_settings", lambda: Settings(None))
    assert quick_view.enabled() is True
    monkeypatch.setattr(app_identity, "user_settings", lambda: Settings("false"))
    assert quick_view.enabled() is False
    monkeypatch.setenv(quick_view.ENV_SWITCH, "1")
    assert quick_view.enabled() is True
    monkeypatch.setenv(quick_view.ENV_SWITCH, "0")
    monkeypatch.setattr(app_identity, "user_settings", lambda: Settings(None))
    assert quick_view.enabled() is False


def test_a_photo_launch_goes_to_the_viewer_and_anything_else_to_the_library(monkeypatch, tmp_path):
    from image_triage import main as entry
    from image_triage.qml_shell import app as qml

    photo = jpeg(tmp_path / "p.jpg", 50, 40)
    viewed, library = [], []
    monkeypatch.setattr(quick_view, "run", lambda path: viewed.append(path) or 0)
    monkeypatch.setattr(qml, "main", lambda args: library.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["ImageTriage.exe", str(photo)])
    assert entry.main() == 0 and viewed == [os.path.abspath(photo)] and library == []
    monkeypatch.setattr(sys, "argv", ["ImageTriage.exe", str(tmp_path)])
    assert entry.main() == 0 and library[-1] == ["--folder", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", ["ImageTriage.exe", "--folder", str(tmp_path), "--select", str(photo)])
    assert entry.main() == 0 and library[-1] == ["--folder", str(tmp_path), "--select", str(photo)], "Library's own launch is not a viewer launch"
    monkeypatch.setattr(quick_view, "run", lambda path: None)            # the viewer is off: the library opens the photo
    monkeypatch.setattr(sys, "argv", ["ImageTriage.exe", str(photo)])
    assert entry.main() == 0 and library[-1] == ["--folder", str(tmp_path), "--select", str(photo)]
    assert len(viewed) == 1


def test_library_starts_the_same_program_on_the_folder_and_photo(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/image-triage/ImageTriage")
    assert quick_view.full_app_command("/shoot/a.jpg") == ["/opt/image-triage/ImageTriage", "--folder", "/shoot", "--select", "/shoot/a.jpg"]
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert quick_view.full_app_command("/shoot/a.jpg")[1:3] == ["-m", "image_triage"]


# ---- the window -----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from PySide6.QtQml import QQmlApplicationEngine

    from image_triage.qml_shell import single_instance
    from image_triage.quick_view.viewer import QuickViewer

    monkeypatch.setattr(single_instance, "server_name", lambda: "it-quick-view-test-" + tmp_path.name)
    monkeypatch.setattr("image_triage.quick_view.viewer.warm_library_files", lambda *a: 0)
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for i, color in enumerate(("#ff0000", "#00ff00", "#0000ff")):
        jpeg(shoot / f"IMG_{i + 1}.jpg", 3000, 2000, color)
    launched = []
    viewer = QuickViewer(str(shoot / "IMG_2.jpg"), None, longest=1200, launcher=launched.append)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i.toString()) for i in items))
    engine.addImageProvider("quick", viewer.provider)
    engine.rootContext().setContextProperty("viewer", viewer)
    viewer.start()
    engine.load(quick_view.QML_FILE.as_uri())
    assert engine.rootObjects(), warnings
    win = engine.rootObjects()[0]
    viewer.attach(win)
    yield viewer, win, shoot, launched, warnings
    win.close()
    viewer.shutdown()
    engine.deleteLater()
    pump(qapp, 0.1)


def test_the_photo_shows_then_the_folder_follows_and_arrows_go_through_it(qapp, window):
    viewer, win, shoot, launched, warnings = window
    photo = win.findChild(QObject, "quickViewPhoto")
    assert wait_for(qapp, lambda: win.property("photoReady") and viewer.listed)
    assert (viewer.name, viewer.index, viewer.count) == ("IMG_2.jpg", 1, 3)
    assert (viewer.photoWidth, viewer.photoHeight) == (3000, 2000)
    assert photo.property("sourceSize").width() == 1200, "decoded at the screen's size, not the photo's"
    viewer.go(1)
    assert wait_for(qapp, lambda: viewer.name == "IMG_3.jpg" and not viewer.loading)
    viewer.go(5)
    assert viewer.index == 2, "it stops at the last photo"
    viewer.goTo(0)
    assert wait_for(qapp, lambda: viewer.name == "IMG_1.jpg" and not viewer.loading)
    assert not [w for w in warnings if "QuickView" in w], warnings


def test_zooming_past_the_screen_size_reads_the_full_photo(qapp, window):
    viewer, win, shoot, launched, warnings = window
    assert wait_for(qapp, lambda: not viewer.loading)
    viewer.wantFull()
    assert wait_for(qapp, lambda: viewer.full and not viewer.loading)
    photo = win.findChild(QObject, "quickViewPhoto")
    assert wait_for(qapp, lambda: photo.property("sourceSize").width() == 3000)


def test_library_starts_the_full_app_and_waits_for_it(qapp, window):
    viewer, win, shoot, launched, warnings = window
    assert wait_for(qapp, lambda: not viewer.loading)
    viewer.openLibrary()
    assert viewer.library == "starting" and launched and launched[0][-2:] == ["--select", str(shoot / "IMG_2.jpg")]
    from image_triage.qml_shell.single_instance import InstanceServer

    server = InstanceServer()                      # the library coming up: it listens
    assert server.listen()
    closed = []
    viewer._close = lambda: closed.append(True)
    assert wait_for(qapp, lambda: closed, seconds=5)


def test_a_running_library_takes_the_photo_at_once(qapp, window, monkeypatch):
    from image_triage.qml_shell import single_instance

    viewer, win, shoot, launched, warnings = window
    sent = []
    monkeypatch.setattr(single_instance, "send_to_running", lambda message, **_: sent.append(message) or True)
    closed = []
    viewer._close = lambda: closed.append(True)
    viewer.openLibrary()
    assert closed and sent[0]["select"] == str(shoot / "IMG_2.jpg") and launched == [], "nothing is started: the running app has it"


def test_warming_reads_the_library_files_within_its_budget(tmp_path):
    from image_triage.quick_view.viewer import warm_library_files

    assert 0 < warm_library_files(budget=2 * 1024 * 1024) <= 3 * 1024 * 1024
