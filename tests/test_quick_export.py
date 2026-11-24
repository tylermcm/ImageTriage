"""Quick Export: a preset, where the copies go, Export. Instagram's shapes are cut from the middle of the photo; the copies go to an Export folder
beside the photos or to PocketDrop; the window opens from the grid's Export…, the viewer and Ctrl+E, and "More options…" opens Batch Export."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QObject, QStandardPaths, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QImage

from image_triage import export_pipeline as pipeline
from image_triage.export_presets import BUILTIN_GROUPS, PresetStore
from image_triage.qml_shell import quick_export_model as quick
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import PhotoModel


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app, condition, seconds=15.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(app, 0.05)
        if condition():
            return True
    return False


# ---- the pipeline's "Crop to size" ----------------------------------------------------------------------------------------------------
def crop(w, h, **more):
    return pipeline.normalized({"sizeMode": "crop", "cropW": str(w), "cropH": str(h), **more})


def test_crop_to_size_cuts_the_middle_to_the_exact_shape():
    values = crop(1080, 1350)
    assert pipeline.crop_box(6000, 4000, values) == (1400, 0, 3200, 4000)          # a landscape photo loses its sides
    assert pipeline.crop_box(4000, 6000, values) == (0, 500, 4000, 5000)           # a tall one loses its top and bottom
    assert pipeline.crop_box(1080, 1350, values) is None                           # already the shape
    assert pipeline.output_size(6000, 4000, values) == (1080, 1350)
    assert pipeline.output_size(3200, 4000, values) == (1080, 1350), "the same before or after the cut"
    assert pipeline.crop_box(6000, 4000, pipeline.normalized({"sizeMode": "long"})) is None


def test_a_photo_smaller_than_the_crop_keeps_its_pixels_in_the_shape_unless_it_may_be_enlarged():
    assert pipeline.output_size(800, 600, crop(1080, 1080)) == (600, 600)
    assert pipeline.output_size(800, 600, crop(1080, 1080, noEnlarge=False)) == (1080, 1080)


def test_the_written_picture_is_the_middle_of_the_photo():
    image = QImage(300, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    for x in range(100, 200):                                # a blue square in the middle
        for y in range(100):
            image.setPixelColor(x, y, QColor("blue"))
    made = pipeline.prepare(image, None, crop(50, 50))
    assert (made.width(), made.height()) == (50, 50)
    corner, middle = made.pixelColor(2, 2), made.pixelColor(25, 25)
    assert corner.blue() > 200 and corner.red() < 60 and middle.blue() > 200, "only the blue middle is kept"
    preview = pipeline.make_preview(image, None, crop(50, 50), side=40)
    assert (preview.width, preview.height) == (50, 50) and preview.after.width() == preview.after.height()


def test_crop_size_problems_block_the_export():
    assert pipeline.size_problem(crop(0, 1350)) == "Enter a size above zero."
    assert pipeline.size_problem(crop(1080, 1350)) == ""


def test_the_instagram_presets_crop_and_every_builtin_has_a_group():
    store = PresetStore(Path("/nonexistent/presets.json"))
    portrait = store.get("Instagram portrait 1080 x 1350")
    assert portrait["sizeMode"] == "crop" and (portrait["cropW"], portrait["cropH"]) == ("1080", "1350")
    assert store.get("Instagram story 1080 x 1920")["cropH"] == "1920"
    assert {p["name"] for p in store.presets() if p["builtin"]} == set(BUILTIN_GROUPS)
    assert quick.describe(store.get("Web 2048 px")) == "Long edge 2048 px · JPEG"
    assert quick.describe(store.get("Archive TIFF 16-bit ProPhoto")) == "Full size · TIFF 16-bit"


# ---- the model ----------------------------------------------------------------------------------------------------------------------------
class FakeDrop(QObject):
    def __init__(self):
        super().__init__()
        self.added = []

    def addPaths(self, paths):
        self.added.extend(paths)


@pytest.fixture
def world(qapp, tmp_path, monkeypatch):
    QStandardPaths.setTestModeEnabled(True)
    monkeypatch.setattr(quick, "pocketdrop_root", lambda: tmp_path / "drop-root")
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for index, name in enumerate(("a", "b", "c")):
        Image.new("RGB", (600, 400), (40 * index, 90, 160)).save(shoot / f"{name}.jpg", "JPEG")
    photos = PhotoModel(parent=qapp)
    toasts = ToastModel(parent=qapp)
    drop = FakeDrop()
    model = quick.QuickExportModel(photos, memory=False, parent=qapp, toasts=toasts, pocket_drop=drop,
                                   store=PresetStore(tmp_path / "presets.json"), settings=tmp_path / "quick.json")
    photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3)
    yield shoot, photos, toasts, drop, model
    QStandardPaths.setTestModeEnabled(False)


def test_quick_export_writes_the_preset_into_an_export_folder_beside_the_photos(qapp, world):
    shoot, photos, toasts, drop, model = world
    model.begin("")
    model.choose("Instagram square 1080 x 1080")
    assert model.count == 3 and model.summary() == "3 photos → shoot/Export".replace("/", __import__("os").sep)
    assert model.run() and model.busy
    assert wait_for(qapp, lambda: not model.busy and any(t["title"] == "Exported 3 photos" for t in toasts.items))
    written = sorted((shoot / "Export").iterdir())
    assert [p.name for p in written] == ["a.jpg", "b.jpg", "c.jpg"]
    with Image.open(written[0]) as image:
        assert image.size == (400, 400), "a 600 x 400 photo is cut to a 400 x 400 square (not enlarged)"
    assert model.run() and wait_for(qapp, lambda: not model.busy and len(list((shoot / "Export").iterdir())) == 6)
    assert (shoot / "Export" / "a_1.jpg").is_file(), "a second export never writes over the first"
    assert drop.added == []


def test_one_photo_alone_and_the_choices_are_remembered(qapp, world, tmp_path):
    shoot, photos, toasts, drop, model = world
    model.begin(str(shoot / "b.jpg"))
    model.choose("Small 800 px")
    model.setDest("folder")
    assert model.count == 1 and model.summary().startswith("This photo")
    assert model.run() and wait_for(qapp, lambda: not model.busy and (shoot / "Export" / "b.jpg").is_file())
    with Image.open(shoot / "Export" / "b.jpg") as image:
        assert image.size == (600, 400), "800 px on the long edge would enlarge it, and presets don't enlarge"
    again = quick.QuickExportModel(photos, memory=False, parent=qapp, store=PresetStore(tmp_path / "presets.json"), settings=tmp_path / "quick.json")
    assert again.preset == "Small 800 px" and again.dest == "folder"
    again.choose("No such preset")
    assert again.preset == "Small 800 px"


def test_pocketdrop_gets_the_copies_and_the_folders_are_left_alone(qapp, world, tmp_path):
    shoot, photos, toasts, drop, model = world
    sent = []
    model.sentToPocketDrop.connect(sent.append)
    model.begin("")
    model.choose("Email 1200 px")
    model.setDest("pocketdrop")
    assert "PocketDrop" in model.summary()
    assert model.run() and wait_for(qapp, lambda: not model.busy and sent == [3])
    assert len(drop.added) == 3 and all(Path(p).is_relative_to(tmp_path / "drop-root") for p in drop.added)
    assert not (shoot / "Export").exists()
    assert any(t["title"] == "3 photos ready in PocketDrop" for t in toasts.items)


def test_old_pocketdrop_copies_are_cleared_when_a_new_export_is_made(tmp_path, monkeypatch):
    monkeypatch.setattr(quick, "pocketdrop_root", lambda: tmp_path / "root")
    first = quick._new_pocketdrop_folder(now=1_000_000)
    (first / "x.jpg").write_bytes(b"x")
    import os

    os.utime(first, (1_000_000, 1_000_000))
    second = quick._new_pocketdrop_folder(now=1_000_000 + quick.POCKETDROP_KEEP_SECONDS + 60)
    assert second.is_dir() and not first.exists()


def test_nothing_to_export_does_nothing(qapp, tmp_path):
    photos = PhotoModel(parent=qapp)
    model = quick.QuickExportModel(photos, memory=False, parent=qapp, store=PresetStore(tmp_path / "p.json"), settings=tmp_path / "q.json")
    model.begin("")
    assert model.count == 0 and model.summary() == "No photos to export." and model.run() is False


# ---- the window ---------------------------------------------------------------------------------------------------------------------------
def test_the_window_opens_from_ctrl_e_and_more_options_opens_batch_export_with_the_preset(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from test_qml_shell import _load_main

    engine, window, warnings = _load_main(qapp, tmp_path)
    try:
        window.setWidth(1440)
        window.setHeight(900)
        context = engine.rootContext()
        from test_qml_shell import rows

        context.contextProperty("photos").setRows([dict(r, decision="", rating=0) for r in rows(6)], "/p")
        pump(qapp, 0.5)
        dialog = window.findChild(QObject, "quickExportDialog")
        assert dialog is not None and dialog.property("shown") is False
        QTest.keyClick(window, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
        pump(qapp, 0.5)
        assert dialog.property("shown") is True
        model = context.contextProperty("quickExport")
        model.choose("Instagram portrait 1080 x 1350")
        QTest.keyClick(window, Qt.Key.Key_Down)
        pump(qapp, 0.2)
        assert model.preset == "Instagram square 1080 x 1080"
        dialog.moreOptions()
        pump(qapp, 0.5)
        batch = context.contextProperty("batch")
        card = window.findChild(QObject, "batchWindow")
        assert dialog.property("shown") is False and card.property("shown") is True and card.property("mode") == "export"
        assert batch.get("sizeMode") == "crop" and batch.get("cropH") == "1080"
        assert not [w for w in warnings if "QuickExport" in w or "BatchWindow" in w], warnings
    finally:
        window.close()
        engine.deleteLater()
        pump(qapp, 0.2)
