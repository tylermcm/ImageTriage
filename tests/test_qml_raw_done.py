"""Done in the RAW editor: the edit is saved in the background, and until that save lands the viewer shows the picture as it was
edited (the editor hands it over), so the edits never seem to vanish; the saved render then takes its place. Also: a depth map made
ahead of time (nothing waiting on it) does not sweep the AI overlay over the photo."""
import threading

from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage

from image_triage.qml_shell import edit_saves
from test_qml_raw_super_res import editing, until
from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401


def test_after_done_the_viewer_keeps_the_edited_picture_until_the_save_lands(shell, qapp, tmp_path):
    window, raw, warnings = editing(shell, qapp)
    host = raw.parent()
    host._path = raw.photoPath                                             # the viewer shows the photo being edited
    gate = threading.Event()
    rendered = tmp_path / "render.jpg"
    edited = QImage(800, 500, QImage.Format_RGB32)
    edited.fill(QColor("#203040"))
    edited.save(str(rendered))

    def slow_save(path, original, draft):
        gate.wait(5)
        return rendered

    saves = edit_saves.EditSaves(slow_save)
    raw._memory = False                                                    # a real editor, not the demo's
    if raw._saves is not None:
        raw._saves.landed.disconnect(raw._landed)
    raw._saves = saves
    saves.landed.connect(raw._landed)
    raw.adjust("exposure", 1.0)
    pump(qapp, .3)
    path = raw.photoPath
    raw.done()
    pump(qapp, .2)
    assert not raw.active and saves.pending(path)
    shown = host.property("src")
    assert shown.startswith("image://rawpreview/thumb/done/")             # the edited picture, while the save runs
    gate.set()
    assert until(qapp, lambda: host.property("src").endswith("render.jpg"))   # then the saved render


def test_a_depth_map_made_ahead_does_not_show_the_ai_overlay(shell, qapp):
    window, raw, warnings = editing(shell, qapp)
    air = raw.atmosphereEditor
    overlay = window.findChild(QObject, "aiWorkingOverlay")
    air._busy, air._pending = True, None                                   # prepared when the panel loaded
    air.changed.emit()
    pump(qapp, .2)
    assert air.background and not overlay.property("visible")
    air._pending = {"amount": 40.0}                                        # an edit now waits on it
    air.changed.emit()
    pump(qapp, .2)
    assert not air.background and overlay.property("title") == "Atmosphere"
    air._busy, air._pending = False, None
    air.changed.emit()


def test_an_ai_tools_error_shows_its_fix_beside_it(shell, qapp):
    from test_qml_raw_crop import prepare

    window, raw, dock, _, warnings = prepare(shell, qapp)
    dock.setProperty("tool", "Remove")
    raw.retouchEditor.choose("erase")
    eraser = raw.eraseEditor
    eraser._error = "MaskEngine host script is missing."
    eraser.changed.emit()
    pump(qapp, .4)
    buttons = [b for b in window.findChildren(QObject, "aiFixButton") if b.property("visible") and b.property("text")]
    assert [b.property("text") for b in buttons] == ["Repair Image Triage"]
    eraser._error = "The fill model did not answer."                       # anything else from an AI tool: the AI Center
    eraser.changed.emit()
    pump(qapp, .3)
    assert [b.property("text") for b in window.findChildren(QObject, "aiFixButton") if b.property("visible") and b.property("text")] == ["Fix in AI Center"]
    eraser._error = ""
    eraser.changed.emit()
    assert not [w for w in warnings if "AiFixButton" in w], warnings
