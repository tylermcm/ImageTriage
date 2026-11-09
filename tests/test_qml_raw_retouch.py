"""Retouch stroke recipes, crop mapping, history and actual QML pointer routing."""
from copy import deepcopy
import pytest
from PySide6.QtCore import QObject,QPointF,Qt
from PySide6.QtTest import QTest
from image_triage.raw_retouch import valid,fits
from image_triage.qml_shell.raw_editor import RawEditor
from test_qml_shell_viewer import shell,qapp,pump
from test_qml_raw_crop import prepare

def test_retouch_source_crop_history_and_validation(qapp):
    raw=RawEditor(memory=True);raw.open('sample.NEF');raw._full_size=[6000,4000]
    model=raw.retouchEditor;raw.setCrop(.2,.1,.5,.6)
    model.choose('clone');model.stroke([[.5,.5]])
    assert not model.rows and 'Alt-click' in raw.error
    model.pick(.2,.3);model.stroke([[.5,.5],[.6,.6]])
    stroke=model.rows[0]
    assert stroke['source']==pytest.approx([.3,.28])
    assert stroke['points'][0]==pytest.approx([.45,.4])
    assert valid(model.rows) and fits(stroke,6000,4000)
    raw.undo();assert not model.rows
    raw.redo();assert len(model.rows)==1
    raw.reset();assert len(model.rows)==1
    raw.open('other.NEF');assert model.source==[] and not model.rows
    raw.open('sample.NEF');assert len(model.rows)==1 and model.source==[]
    model.clear();assert not model.rows
    raw.undo();assert len(model.rows)==1
    raw.shutdown()

@pytest.mark.parametrize('change',[{'kind':'bad'},{'source':None},{'points':[[float('nan'),.2]]},{'points':[]},{'size':10},{'feather':-1},{'opacity':101}])
def test_bad_retouch_rejected(change):
    item={'kind':'clone','source':[.1,.1],'points':[[.5,.5]],'size':.05,'feather':50,'opacity':100}
    assert not valid([{**item,**change}])

def test_qml_clone_alt_source_and_stroke(shell,qapp):
    window,raw,dock,_,warnings=prepare(shell,qapp)
    dock.setProperty('tool','Remove');pump(qapp,.15)
    pending=[window.contentItem()];button=None
    while pending:
        item=pending.pop()
        if item.objectName()=='retouchTool_clone':button=item;break
        pending.extend(item.childItems())
    assert button is not None
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint());pump(qapp,.2)
    assert raw.retouchEditor.kind=='clone'
    assert button.property('variant')=='primary'
    overlay=window.findChild(QObject,'rawRetouchOverlay')
    pointer=window.findChild(QObject,'rawRetouchInput')
    def point(x,y):return pointer.mapToScene(QPointF(x*pointer.width(),y*pointer.height())).toPoint()
    QTest.mouseClick(window,Qt.LeftButton,Qt.AltModifier,point(.2,.3));pump(qapp,.08)
    assert raw.retouchEditor.source==pytest.approx([.2,.3],abs=.01)
    QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,point(.5,.5))
    QTest.mouseMove(window,point(.6,.6),30)
    QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,point(.6,.6));pump(qapp,.1)
    assert len(raw.retouchEditor.rows)==1 and raw.retouchEditor.rows[0]['kind']=='clone'
    assert raw.retouchEditor.rows[0]['points'][0]==pytest.approx([.5,.5],abs=.01)
    assert not [w for w in warnings if 'Retouch' in w],warnings


def test_retouch_bounds_and_transparent_identity(qapp):
    item={'kind':'remove','points':[[0,0],[1,1]],'size':.5,'feather':50,'opacity':100}
    assert not fits(item,8000,6000)
    raw=RawEditor(memory=True);raw.open('large.NEF');raw._full_size=[8000,6000]
    raw.retouchEditor.option('size',50);raw.retouchEditor.stroke([[0,0],[1,1]])
    assert not raw.retouchEditor.rows and 'separate strokes' in raw.error
    raw.shutdown()


def visual_item(window, name):
    pending = [window.contentItem()]
    while pending:
        item = pending.pop()
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
    raise AssertionError(name)


def click_item(window, item):
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


@pytest.mark.parametrize("kind", ["remove", "spot", "heal", "clone"])
def test_zoomed_retouch_cannot_steal_sidebar_buttons(shell, qapp, kind):
    window, raw, dock, _, warnings = prepare(shell, qapp)
    dock.setProperty("tool", "Remove")
    viewer = window.findChild(QObject, "viewer")
    viewer.setProperty("zoomScale", 1.0)
    viewer.setProperty("zoomed", True)
    raw.retouchEditor.choose(kind)
    raw.retouchEditor.pick(.2, .3)
    pump(qapp, .5)
    stage = window.findChild(QObject, "viewerPhotoStage")
    overlay = window.findChild(QObject, "rawRetouchOverlay")
    assert overlay.width() > stage.width() * 2
    assert overlay.parentItem() == stage and stage.clip()
    other = "heal" if kind == "clone" else "clone"
    button = visual_item(window, "retouchTool_" + other)
    QTest.mouseMove(window, button.mapToScene(QPointF(20, 15)).toPoint())
    pump(qapp, .05)
    assert not window.findChild(QObject, "rawRetouchCursor").isVisible()
    click_item(window, button)
    pump(qapp, .1)
    assert raw.retouchEditor.kind == other
    assert not raw.retouchEditor.rows
    click_item(window, visual_item(window, "rawTool_Adjust"))
    pump(qapp, .1)
    assert dock.property("tool") == "Adjust"
    assert not [w for w in warnings if "Retouch" in w], warnings


def test_retouch_drag_stops_at_viewport_and_releases_controls(shell, qapp):
    window, raw, dock, _, warnings = prepare(shell, qapp)
    dock.setProperty("tool", "Remove")
    viewer = window.findChild(QObject, "viewer")
    viewer.setProperty("zoomScale", 1.0)
    viewer.setProperty("zoomed", True)
    pump(qapp, .5)
    stage = window.findChild(QObject, "viewerPhotoStage")
    start = stage.mapToScene(QPointF(stage.width()*.7, stage.height()*.5)).toPoint()
    outside = stage.mapToScene(QPointF(stage.width()+40, stage.height()*.5)).toPoint()
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(window, outside, 30)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, outside)
    pump(qapp, .1)
    assert len(raw.retouchEditor.rows) == 1
    # No off-viewport endpoint was appended while the mouse was grabbed.
    overlay = window.findChild(QObject, "rawRetouchOverlay")
    for x, y in raw.retouchEditor.rows[0]["points"]:
        p = overlay.mapToItem(stage, QPointF(x*overlay.width(), y*overlay.height()))
        assert 0 <= p.x() < stage.width() and 0 <= p.y() < stage.height()
    assert not overlay.property("drawing")
    click_item(window, visual_item(window, "retouchTool_heal"))
    pump(qapp, .1)
    assert raw.retouchEditor.kind == "heal"


def test_retouch_ignores_letterbox_clicks(shell, qapp):
    window, raw, dock, _, warnings = prepare(shell, qapp)
    dock.setProperty("tool", "Remove")
    pump(qapp, .5)
    stage = window.findChild(QObject, "viewerPhotoStage")
    overlay = window.findChild(QObject, "rawRetouchOverlay")
    origin = overlay.mapToItem(stage, QPointF(0, 0))
    assert max(origin.x(), origin.y()) > 5
    margin = QPointF(2, stage.height()/2) if origin.x() > 5 else QPointF(stage.width()/2, 2)
    outside = stage.mapToScene(margin).toPoint()
    QTest.mouseMove(window, outside)
    QTest.mouseClick(window, Qt.LeftButton, Qt.AltModifier, outside)
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, outside)
    pump(qapp, .1)
    assert not raw.retouchEditor.source and not raw.retouchEditor.rows
    assert not window.findChild(QObject, "rawRetouchCursor").isVisible()
