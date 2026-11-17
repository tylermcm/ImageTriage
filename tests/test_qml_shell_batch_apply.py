"""Batch rename and export really apply, off the UI thread, with a toast, and a rename can be undone."""
from __future__ import annotations

import time

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths
from PySide6.QtGui import QGuiApplication

from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell.batch_model import BatchModel
from image_triage.qml_shell.decision_bridge import DecisionBridge
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


def wait_for(app, condition, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(app, 0.05)
        if condition():
            return True
    return False


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    QStandardPaths.setTestModeEnabled(True)
    monkeypatch.setattr(bridge_module, "session_id", lambda: "qml-batch-" + tmp_path.name)
    yield
    QStandardPaths.setTestModeEnabled(False)


@pytest.fixture
def world(qapp, tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for index, name in enumerate(("a", "b", "c")):
        Image.new("RGB", (400 + index * 10, 300), (30 * index, 90, 160)).save(shoot / f"{name}.jpg", "JPEG")
    photos = PhotoModel(parent=qapp)
    toasts = ToastModel(parent=qapp)
    bridge = DecisionBridge(photos, toasts=toasts, parent=qapp)
    batch = BatchModel(photos, memory=False, parent=qapp, toasts=toasts)
    photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3)
    return shoot, photos, toasts, bridge, batch


def names(folder):
    return sorted(p.name for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".png"))


def test_rename_renames_the_files_keeps_the_decisions_and_undoes(qapp, world):
    shoot, photos, toasts, bridge, batch = world
    photos.setDecision(next(i for i in range(photos.shown) if photos.get(i)["name"] == "a.jpg"), "win")
    bridge.flush()
    pump(qapp, 0.3)
    batch.begin("rename")
    batch.set("prefix", "wed_")
    assert batch.canApply
    batch.apply()
    assert batch.busy
    assert wait_for(qapp, lambda: not batch.busy and any(t["title"].startswith("Renamed") for t in toasts.items))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3 and all(photos.get(i)["name"].startswith("wed_") for i in range(photos.shown)))
    assert all(n.startswith("wed_") for n in names(shoot)) and len(names(shoot)) == 3
    winner = next(photos.get(i) for i in range(photos.shown) if photos.get(i)["decision"] == "win")
    assert winner["name"].endswith("a.jpg") or "a" in winner["name"]                     # the decision followed its file
    assert photos.undo().startswith("rename of 3")
    assert wait_for(qapp, lambda: names(shoot) == ["a.jpg", "b.jpg", "c.jpg"])
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3 and photos.winCount == 1)


def test_export_writes_sized_copies_in_a_folder_and_leaves_the_originals(qapp, world):
    shoot, photos, toasts, bridge, batch = world
    batch.begin("export")
    batch.set("sizeMode", "long")
    batch.set("long", "200")
    assert batch.canApply, batch.foot
    batch.apply()
    assert wait_for(qapp, lambda: any(t["title"].startswith("Exported") for t in toasts.items))
    assert names(shoot) == ["a.jpg", "b.jpg", "c.jpg"]
    out = shoot / "Export"
    assert names(out) == ["a.jpg", "b.jpg", "c.jpg"]
    with Image.open(out / "c.jpg") as copy:
        assert max(copy.size) == 200 and copy.info.get("icc_profile")         # sRGB, embedded


def test_export_writes_the_chosen_format(qapp, world):
    shoot, photos, toasts, bridge, batch = world
    batch.begin("export")
    batch.set("format", "png")
    batch.apply()
    assert wait_for(qapp, lambda: any(t["title"].startswith("Exported") for t in toasts.items))
    assert names(shoot / "Export") == ["a.png", "b.png", "c.png"]


def test_a_second_apply_while_one_runs_does_nothing(qapp, world):
    shoot, photos, toasts, bridge, batch = world
    batch.begin("export")
    batch.apply()
    batch.apply()
    assert wait_for(qapp, lambda: any(t["title"].startswith("Exported") for t in toasts.items))
    pump(qapp, 0.3)
    assert len([t for t in toasts.items if t["title"].startswith("Exported")]) == 1


def test_the_preview_shows_the_selected_photo_as_it_will_be_written(qapp, world):
    shoot, photos, toasts, bridge, batch = world
    batch.begin("export")
    batch.set("sizeMode", "long")
    batch.set("long", "100")
    assert wait_for(qapp, lambda: not batch.previewBusy and batch.previewAfter != "")
    assert batch.previewInfo.startswith("100 x 75 px") and batch.previewName == "a.jpg" and batch.previewCount == 3
    batch.stepPreview(1)
    assert wait_for(qapp, lambda: not batch.previewBusy and batch.previewName == "b.jpg")
    assert batch.previewInfo.startswith("100 x 73 px")
    batch.setPreviewIndex(99)
    assert batch.previewIndex == 2


def test_a_plan_with_errors_is_not_applied(qapp, world):
    shoot, photos, toasts, bridge, batch = world
    batch.begin("rename")
    batch.set("newName", "x/y")                       # a path is not a file name
    assert not batch.canApply
    batch.apply()
    assert not batch.busy and names(shoot) == ["a.jpg", "b.jpg", "c.jpg"]


def test_sample_photos_are_only_counted(qapp):
    photos = PhotoModel(parent=qapp)
    photos.setRows([{"path": "x/a.jpg", "name": "a", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 1}], "sample")
    batch = BatchModel(photos, memory=True, parent=qapp, toasts=ToastModel(parent=qapp))
    seen = []
    batch.applied.connect(lambda mode, count: seen.append((mode, count)))
    batch.begin("rename")
    batch.set("prefix", "p_")
    batch.apply()
    assert seen and not batch.busy and batch.sampleNote == "Sample photos: no file will change."
    batch.cleanup()
