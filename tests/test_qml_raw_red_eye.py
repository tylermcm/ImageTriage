"""Red Eye recipe, history, crop mapping and real pointer routing."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from image_triage.raw_red_eye import valid
from image_triage.qml_shell.raw_editor import RawEditor
from image_triage import photocraft_bridge as bridge
from test_qml_shell_viewer import shell, qapp, pump
from test_qml_raw_crop import prepare
from test_qml_raw_retouch import visual_item, click_item


def test_red_eye_crop_history_selection_and_sessions(qapp):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw.setCrop(.2, .1, .5, .6)
    model = raw.redEyeEditor
    model.correct(.5, .5)
    assert model.rows[0]["point"] == pytest.approx([.45, .4])
    assert model.selected == 0 and valid(model.rows)
    model.option("pupilSize", 70)
    model.option("darken", 85)
    assert model.rows[0]["pupilSize"] == 70 and model.rows[0]["darken"] == 85
    raw.undo()
    assert model.darken == 50
    raw.redo()
    assert model.darken == 85
    raw.reset()
    assert len(model.rows) == 1
    raw.open("other.NEF")
    assert not model.rows and model.selected == -1
    raw.open("sample.NEF")
    assert len(model.rows) == 1 and model.selected == -1
    model.clear()
    assert not model.rows
    raw.undo()
    assert len(model.rows) == 1
    model.remove(0)
    assert not model.rows
    raw.shutdown()


@pytest.mark.parametrize("change", [{"point":[-1, .5]}, {"point":[float("nan"), .5]},
    {"point":[.2]}, {"pupilSize":0}, {"darken":101}, {"darken":True}, {"extra":1}])
def test_invalid_corrections_rejected(change):
    assert not valid([{**{"point":[.5,.5],"pupilSize":50,"darken":50},**change}])


def test_red_eye_capability_prevents_older_editor_losing_corrections():
    control = object.__new__(bridge.PhotoCraftControl)
    control.camera_raw_supported = True
    control.call = Mock(return_value={})
    spec = {"recipe":{"version":1,"cameraRaw":{"redEye":[{"point":[.5,.5],"pupilSize":50,"darken":50}]}}}
    with pytest.raises(bridge.PhotoCraftCompatibilityError, match="Red Eye"):
        control.app_open("sample.nef", camera_raw=spec)
    control.call.assert_not_called()
    control.camera_raw_red_eye_supported = True
    control.app_open("sample.nef", camera_raw=spec)
    assert control.call.call_args.args[1]["cameraRaw"] == spec


def test_red_eye_zoomed_click_and_sidebar_are_independent(shell, qapp):
    window, raw, dock, _, warnings = prepare(shell, qapp)
    dock.setProperty("tool", "Red Eye")
    viewer = window.findChild(QObject, "viewer")
    viewer.setProperty("zoomScale", 1.0)
    viewer.setProperty("zoomed", True)
    pump(qapp, .5)
    stage = window.findChild(QObject, "viewerPhotoStage")
    overlay = window.findChild(QObject, "rawRedEyeOverlay")
    assert overlay.parentItem() == stage and overlay.width() > stage.width()*2
    pos = stage.mapToScene(QPointF(stage.width()*.5, stage.height()*.5)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, pos)
    pump(qapp, .1)
    assert len(raw.redEyeEditor.rows) == 1
    assert viewer.property("zoomed")  # A pupil click must not toggle fit/100%.
    selected = visual_item(window, "redEyeCorrection_0")
    QTest.mouseMove(window, selected.mapToScene(QPointF(20, 15)).toPoint())
    pump(qapp, .05)
    assert not window.findChild(QObject, "rawRedEyeCursor").isVisible()
    before = deepcopy(raw.redEyeEditor.rows)
    click_item(window, selected)
    click_item(window, visual_item(window, "rawTool_Adjust"))
    pump(qapp, .1)
    assert dock.property("tool") == "Adjust" and raw.redEyeEditor.rows == before
    assert not [w for w in warnings if "RedEye" in w], warnings


def test_red_eye_ignores_letterbox_and_off_viewport_clicks(shell, qapp):
    window, raw, dock, _, warnings = prepare(shell, qapp)
    dock.setProperty("tool", "Red Eye")
    pump(qapp, .5)
    stage = window.findChild(QObject, "viewerPhotoStage")
    overlay = window.findChild(QObject, "rawRedEyeOverlay")
    origin = overlay.mapToItem(stage, QPointF(0, 0))
    assert max(origin.x(), origin.y()) > 5
    margin = QPointF(2,stage.height()/2) if origin.x()>5 else QPointF(stage.width()/2,2)
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,stage.mapToScene(margin).toPoint())
    pump(qapp,.1)
    assert not raw.redEyeEditor.rows
    raw.redEyeEditor.correct(-.1,.5)
    assert not raw.redEyeEditor.rows


def test_correction_limit_and_busy_guard(qapp):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    model = raw.redEyeEditor
    raw._proxy_pending = True
    model.correct(.5,.5)
    assert not model.rows
    raw._proxy_pending = False
    raw._draft["redEye"] = [{"point":[.5,.5],"pupilSize":50,"darken":50} for _ in range(64)]
    model.correct(.2,.3)
    assert len(model.rows) == 64 and "64 corrections" in raw.error
    raw.shutdown()


def test_older_preview_helper_refuses_red_eye_before_writing():
    from image_triage.qml_shell.raw_preview import Connection
    connection = object.__new__(Connection)
    connection.proc = Mock()
    request = {"op":"render","recipe":{"cameraRaw":{"redEye":[{"point":[.5,.5],"pupilSize":50,"darken":50}]}}}
    with pytest.raises(RuntimeError,match="updated RAW preview"):
        connection.call(request)
    connection.proc.stdin.write.assert_not_called()


def test_red_eye_keeps_scroll_wheel_zoom(shell, qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QGuiApplication, QWheelEvent
    window, raw, dock, _, warnings = prepare(shell, qapp)
    dock.setProperty("tool", "Red Eye")
    pump(qapp, .4)
    stage = window.findChild(QObject, "viewerPhotoStage")
    viewer = window.findChild(QObject, "viewer")
    point = stage.mapToScene(QPointF(stage.width()/2, stage.height()/2))
    event = QWheelEvent(point, QPointF(window.mapToGlobal(point.toPoint())), QPoint(), QPoint(0,120),
                        Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    QGuiApplication.sendEvent(window, event)
    pump(qapp, .1)
    assert viewer.property("zoomed")
    assert not raw.redEyeEditor.rows
