"""Large reject operations yield to Qt and publish one grid/undo update."""
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtTest import QSignalSpy

from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


def wait_done(qapp, cull):
    deadline = time.monotonic() + 5
    while cull.applying and time.monotonic() < deadline:
        pump(qapp, .01)
    assert not cull.applying


def test_large_apply_yields_batches_and_has_one_exact_undo(qapp):
    photos = PhotoModel(parent=qapp)
    source = [dict(row, decision="") for row in rows(2000)]
    source[0]["decision"] = "win"
    source[1]["decision"] = "rej"
    source[2]["decision"] = "keep"
    photos.setRows(source, "/p")
    model = CullModel(photos, memory=True, parent=qapp)
    model._flags = {row["path"]: "soft" for row in source[:1463]}
    model._build_stacks()
    model._set(done=True, page="apply", window="cull")
    before = [(row["decision"], row["rating"]) for row in photos.all_rows()]
    counts, history = QSignalSpy(photos.countsChanged), QSignalSpy(photos.undoChanged)
    beats = []
    timer = QTimer(); timer.setInterval(1); timer.timeout.connect(lambda: beats.append(True)); timer.start()
    model.apply()
    job = model._apply_job
    assert model.applying and counts.count() == 0 and not photos.canUndo
    model.apply(); model.close(); model.hide(); model.undo()
    assert model._apply_job is job and model.view["window"] == "cull"
    wait_done(qapp, model)
    timer.stop()
    assert len(beats) > 10  # Other Qt events run between the 32-photo batches.
    assert photos.rejCount == 1462 and photos.winCount == 1
    assert counts.count() == history.count() == 1
    assert len(photos._history) == 1  # Not the old last-200 individual decisions.
    assert model.view["page"] == "done"
    photos.undo()
    assert [(row["decision"], row["rating"]) for row in photos.all_rows()] == before


def test_shutdown_finishes_pending_updates_before_persistence_flush(qapp):
    photos = PhotoModel(parent=qapp)
    source = [dict(row, decision="") for row in rows(90)]
    photos.setRows(source, "/p")
    model = CullModel(photos, memory=True, parent=qapp)
    model._flags = {row["path"]: "soft" for row in source}
    model._set(done=True, page="apply")
    model.apply()
    model.shutdown()
    assert not model.applying and not model._apply_timer.isActive()
    assert photos.rejCount == 90
    model.undo()
    assert photos.rejCount == 0


def test_already_rejected_photos_are_not_toggled_or_added_to_undo(qapp):
    photos = PhotoModel(parent=qapp)
    source = [dict(row, decision="rej") for row in rows(3)]
    photos.setRows(source, "/p")
    model = CullModel(photos, memory=True, parent=qapp)
    model._flags = {row["path"]: "soft" for row in source}
    model._set(done=True, page="apply")
    model.apply()
    wait_done(qapp, model)
    assert photos.rejCount == 3 and not photos.canUndo and not model.view["canUndo"]
    model.undo()
    assert photos.rejCount == 3


def test_overlay_spins_inside_the_cull_window_while_applying(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    photos = engine.rootContext().contextProperty("photos")
    source = [dict(row, decision="") for row in rows(160)]
    photos.setRows(source, "/p")
    model = engine.rootContext().contextProperty("cull")
    model.scene("cu-apply")
    model._flags = {row["path"]: "soft" for row in source}
    model._rebuild()
    pump(qapp, .4)
    model.apply()
    overlay = window.findChild(QObject, "cullApplyOverlay")
    spinner = window.findChild(QObject, "cullApplySpinner")
    assert overlay.isVisible() and spinner.property("spinning") and spinner.property("size") == 160
    # Pause the operation timer to inspect the animation without changing its code.
    model._apply_timer.stop()
    pump(qapp, .1)
    first = spinner.property("iris")
    pump(qapp, .15)
    assert spinner.property("iris") != first
    content = next(item for item in overlay.parentItem().childItems() if item.property("softwareRendering") is not None)
    assert not content.isEnabled()
    model._apply_timer.start(0)
    wait_done(qapp, model)
    assert not overlay.isVisible() and not spinner.property("spinning")
    assert not [w for w in warnings if "CullWindow.qml" in w], warnings
    window.close(); engine.deleteLater(); pump(qapp, .2)
