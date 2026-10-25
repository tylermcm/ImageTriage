"""Running a handoff recipe for real: export, archive, copy and move, through the runner and through the transfer cards."""
from __future__ import annotations

import time
import zipfile

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths
from PySide6.QtGui import QGuiApplication

from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell import file_actions as fa
from image_triage.qml_shell.decision_bridge import DecisionBridge
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.transfer_model import TransferModel
from image_triage.qml_shell.workflow_runner import WorkflowRunner, _TransferTask
from image_triage.workflows import (
    RECIPE_CONTENT_BUNDLE,
    RECIPE_CONTENT_EXPORT,
    RECIPE_TRANSFER_ARCHIVE,
    RECIPE_TRANSFER_COPY,
    RECIPE_TRANSFER_MOVE,
    WorkflowRecipe,
)


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
    session = "qml-wf-" + tmp_path.name
    monkeypatch.setattr(bridge_module, "session_id", lambda: session)
    monkeypatch.setattr(fa, "session_id", lambda: session)
    yield
    QStandardPaths.setTestModeEnabled(False)


@pytest.fixture
def world(qapp, tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for index, name in enumerate(("a", "b", "c")):
        Image.new("RGB", (640, 480), (40 * index, 90, 160)).save(shoot / f"{name}.jpg", "JPEG")
    (shoot / "a.nef").write_bytes(b"raw-a")
    photos = PhotoModel(parent=qapp)
    toasts = ToastModel(parent=qapp)
    bridge = DecisionBridge(photos, toasts=toasts, parent=qapp)
    photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3)
    records = [r["record"] for r in photos._rows]
    return shoot, photos, toasts, bridge, records, tmp_path


def recipe(**kw):
    base = dict(key="t", name="Test", content_mode=RECIPE_CONTENT_BUNDLE, transfer_mode=RECIPE_TRANSFER_COPY, destination_subfolder="out")
    base.update(kw)
    return WorkflowRecipe(**base)


def run(app, runner, rec, records, root, folder):
    done, failed, progress = [], [], []
    runner.finished.connect(done.append)
    runner.failed.connect(failed.append)
    runner.progress.connect(lambda f, n, b: progress.append((f, n, b)))
    started = runner.run(rec, records, str(root), str(folder))
    assert wait_for(app, lambda: done or failed), "the run never finished"
    return done, failed, progress, started


def test_copy_bundles_with_their_companions(qapp, world):
    shoot, photos, toasts, bridge, records, tmp = world
    runner = WorkflowRunner(parent=qapp)
    done, failed, progress, _ = run(qapp, runner, recipe(), records, tmp / "deliver", shoot)
    assert not failed and done[0]["kind"] == "copy" and done[0]["written"] == 3
    out = tmp / "deliver" / "out"
    assert {p.name for p in out.iterdir()} >= {"a.jpg", "a.nef", "b.jpg", "c.jpg"}
    assert (shoot / "a.jpg").exists()                                               # copies leave the originals
    assert progress and progress[-1][0] == 1.0 and progress[-1][2] > 0              # progress reached the end and counted bytes


def test_leaving_the_companions_out_takes_only_each_photos_own_file(qapp, world):
    shoot, photos, toasts, bridge, records, tmp = world
    runner = WorkflowRunner(parent=qapp)
    done, failed, _ = [], [], None
    runner.finished.connect(done.append)
    runner.run(recipe(), records, str(tmp / "alone"), str(shoot), include_companions=False)
    assert wait_for(qapp, lambda: done)
    names = {p.name for p in (tmp / "alone" / "out").iterdir()}
    assert "b.jpg" in names and "a.xmp" not in names and not ({"a.jpg", "a.nef"} <= names)        # a's RAW is the photo; its JPEG partner stayed


def test_grouping_puts_each_photo_in_its_own_folder(qapp, world):
    shoot, photos, toasts, bridge, records, tmp = world
    runner = WorkflowRunner(parent=qapp)
    done, failed, _, _ = run(qapp, runner, recipe(group_by_record_folder=True), records, tmp / "deliver", shoot)
    out = tmp / "deliver" / "out"
    assert sorted(p.name for p in out.iterdir()) == ["a", "b", "c"] and {p.name for p in (out / "a").iterdir()} >= {"a.jpg", "a.nef"}


def test_move_hands_back_what_it_moved(qapp, world):
    shoot, photos, toasts, bridge, records, tmp = world
    runner = WorkflowRunner(parent=qapp)
    done, failed, _, _ = run(qapp, runner, recipe(transfer_mode=RECIPE_TRANSFER_MOVE), records, tmp / "deliver", shoot)
    assert done[0]["kind"] == "move" and len(done[0]["moved"]) == 3
    assert not (shoot / "b.jpg").exists() and (tmp / "deliver" / "out" / "b.jpg").exists()


def test_export_writes_resized_copies_and_can_archive_them(qapp, world):
    shoot, photos, toasts, bridge, records, tmp = world
    runner = WorkflowRunner(parent=qapp)
    rec = recipe(content_mode=RECIPE_CONTENT_EXPORT, resize_preset_key="small", convert_suffix=".png", archive_after_export=True, archive_format="zip")
    plain = [r for r in records if not r.path.endswith(".nef")]               # the fake RAW of the fixture is not a picture
    done, failed, _, _ = run(qapp, runner, rec, plain, tmp / "deliver", shoot)
    assert not failed, failed
    out = tmp / "deliver" / "out"
    pngs = [p for p in out.iterdir() if p.suffix == ".png"]
    assert len(pngs) == 2 and done[0]["archive"].endswith(".zip")
    with zipfile.ZipFile(done[0]["archive"]) as z:
        assert len(z.namelist()) == 2


def test_an_archive_recipe_makes_one_archive_of_every_file(qapp, world):
    shoot, photos, toasts, bridge, records, tmp = world
    runner = WorkflowRunner(parent=qapp)
    done, failed, _, _ = run(qapp, runner, recipe(transfer_mode=RECIPE_TRANSFER_ARCHIVE, destination_subfolder="", archive_format="zip", name="Delivery"), records, tmp / "deliver", shoot)
    assert not failed
    with zipfile.ZipFile(tmp / "deliver" / "Delivery.zip") as z:
        names = {n.rsplit("/", 1)[-1] for n in z.namelist()}
    assert {"a.jpg", "a.nef", "b.jpg", "c.jpg"} <= names


def test_a_recipe_that_cannot_start_says_why(qapp, world):
    shoot, photos, toasts, bridge, records, tmp = world
    runner = WorkflowRunner(parent=qapp)
    failed = []
    runner.failed.connect(failed.append)
    assert runner.run(recipe(), [], str(tmp), str(shoot)) is False and "no photos" in failed[-1]
    assert runner.run(recipe(), records, "", str(shoot)) is False and "destination" in failed[-1]


def test_a_cancelled_copy_stops_between_photos(qapp, world):
    shoot, photos, toasts, bridge, records, tmp = world
    items = [(r, (r.path,), str(tmp / "x")) for r in records]
    task = _TransferTask(items, move=False)
    results = []
    task.signals.finished.connect(results.append)
    task.cancelled = True
    task.run()
    assert results[0]["done"] == [] and results[0]["cancelled"] and not (tmp / "x").exists()


def test_the_cards_follow_a_real_move_and_one_undo_brings_it_all_back(qapp, world):
    from image_triage.decision_store import DecisionStore

    shoot, photos, toasts, bridge, records, tmp = world
    photos.setDecision(next(i for i in range(photos.shown) if photos.get(i)["name"] == "b.jpg"), "win")
    bridge.flush()
    pump(qapp, 0.3)
    handoff = HandoffModel(photos, memory=False, parent=qapp)
    handoff._settings = QSettings(str(tmp / "h.ini"), QSettings.Format.IniFormat)
    runner = WorkflowRunner(parent=qapp)
    transfers = TransferModel(photos, memory=False, parent=qapp, toasts=toasts, runner=runner)
    handoff.jobPrepared.connect(transfers.setJob)
    handoff.runRequested.connect(transfers.handoff)
    handoff.set("content", RECIPE_CONTENT_BUNDLE)
    handoff.set("transfer", RECIPE_TRANSFER_MOVE)
    handoff.set("sub", "moved")
    handoff.set("root", str(tmp / "deliver")) if hasattr(handoff, "set") and "root" in handoff._v else setattr(handoff, "_root", str(tmp / "deliver"))
    handoff.run()
    assert transfers.card["state"] == "confirm" and transfers.card["danger"]            # a move asks first
    assert (shoot / "b.jpg").exists()
    transfers.confirm()
    assert wait_for(qapp, lambda: transfers.card["state"] == "done")
    assert transfers.card["doneTitle"] == "Moved 3 items"
    assert not (shoot / "b.jpg").exists() and (tmp / "deliver" / "moved" / "b.jpg").exists()
    assert photos.total == 0                                                              # the photos left the grid
    assert len(DecisionStore().winner_paths(bridge_module.session_id())) == 1             # the decision follows the file
    assert photos.undo() == "handoff of 3 photos"
    assert wait_for(qapp, lambda: (shoot / "b.jpg").exists() and not (tmp / "deliver" / "moved" / "b.jpg").exists())
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3 and photos.winCount == 1)


def test_the_cards_play_a_script_for_sample_photos(qapp):
    photos = PhotoModel(parent=qapp)
    photos.setRows([{"path": "x/a.jpg", "name": "a", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 1}], "sample")
    transfers = TransferModel(photos, memory=True, parent=qapp, runner=WorkflowRunner(parent=qapp))
    transfers.setJob(None)
    transfers.handoff("copy", 1, "D:/a", "D:/b")
    assert transfers.card["state"] == "progress" and transfers._real is False
    transfers.dismiss()


def test_the_cancel_button_is_only_for_what_can_stop(qapp, world):
    shoot, photos, toasts, bridge, records, tmp = world
    runner = WorkflowRunner(parent=qapp)
    assert runner.can_cancel is False
    transfers = TransferModel(photos, memory=False, parent=qapp, toasts=toasts, runner=runner)
    transfers._state = "progress"
    transfers._real = True
    transfers._rebuild()
    assert transfers.card["canCancel"] is False                                           # nothing running that can stop
