"""Native mask recipes authored from the QML editor, including actual pointer input."""
import numpy as np
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from image_triage.camera_raw_recipe_masks import masks_valid
from image_triage.qml_shell.raw_editor import RawEditor
from image_triage.qml_shell.raw_masks import encode_raster, decode_raster
from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401
from test_qml_raw_crop import prepare


def test_raster_contract_round_trip_and_invalid_rejected():
    image = np.arange(10000, dtype=np.uint8).reshape(100,100)
    shape = encode_raster(image)
    assert masks_valid([{"shape": shape}])
    np.testing.assert_array_equal(decode_raster(shape), image)
    with pytest.raises(ValueError): decode_raster({**shape,"data":"bad"})


def test_masks_crop_coordinates_adjustments_and_history(qapp):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw._full_size = [6000,4000]
    raw.setCrop(.2,.1,.5,.6)
    model = raw.maskEditor
    model.arm("linear");model.draw(0,0,1,1)
    assert model.current["shape"] == {"type":"linear","x0":.2,"y0":.1,"x1":.7,"y1":.7}
    raw.beginGesture()
    for v in (.1,.3,1): model.adjust("exposure",v)
    raw.endGesture()
    assert model.current["adjust"]["exposure"] == 1
    raw.undo()
    assert model.current["adjust"]["exposure"] == 0
    raw.redo()
    assert model.current["adjust"]["exposure"] == 1
    model.toggle("invert");model.toggle("enabled")
    assert model.current["invert"] and not model.current["enabled"]
    model.duplicate();assert len(model.rows)==2
    model.rename("Window");assert model.current["name"]=="Window"
    model.remove();assert len(model.rows)==1
    model.arm("luminanceRange");model.adjust("low",.9)
    assert model.current["shape"]["low"] <= model.current["shape"]["high"]
    assert masks_valid(raw._draft["masks"])
    raw.reset()
    assert len(model.rows) == 2  # Global Reset must not discard authored masks.
    model.adjust("exposure",1)
    model.resetAdjustments()
    assert model.current["adjust"]["exposure"] == 0 and model.current["amount"] == 100
    raw.cancel();assert raw._draft==raw._original
    raw.shutdown()


def test_brush_erase_overlay_and_one_undo_per_stroke(qapp):
    raw = RawEditor(memory=True);raw.open("sample.NEF");raw._full_size=[1200,800]
    model=raw.maskEditor;model.arm("brush")
    model.stroke([[.4,.5],[.6,.5]])
    painted=decode_raster(model.current["shape"])
    assert painted.max()==255 and painted.sum()>0
    assert model.overlay.startswith("image://")
    history=len(raw.history)
    model.brushOptions(12,50,True)
    model.stroke([[.4,.5],[.6,.5]])
    erased=decode_raster(model.current["shape"])
    assert erased.sum()<painted.sum() and len(raw.history)==history+1
    raw.undo();np.testing.assert_array_equal(decode_raster(model.current["shape"]),painted)
    raw.open("second.NEF");assert model.rows==[]
    raw.open("sample.NEF");assert len(model.rows)==1
    raw.shutdown()


def test_pointer_gradients_brush_and_local_slider(shell,qapp):
    window,raw,dock,crop,warnings=prepare(shell,qapp)
    dock.setProperty("tool","Mask");pump(qapp,.2)
    overlay=window.findChild(QObject,"rawMaskOverlay")
    def stroke(kind,a,b):
        raw.maskEditor.arm(kind);pump(qapp,.08)
        start=overlay.mapToScene(QPointF(a[0]*overlay.width(),a[1]*overlay.height())).toPoint()
        end=overlay.mapToScene(QPointF(b[0]*overlay.width(),b[1]*overlay.height())).toPoint()
        QTest.mousePress(window,Qt.LeftButton,Qt.NoModifier,start);pump(qapp,.02)
        QTest.mouseMove(window,end,30);pump(qapp,.02)
        QTest.mouseRelease(window,Qt.LeftButton,Qt.NoModifier,end);pump(qapp,.08)
    stroke("linear",(.2,.2),(.7,.8))
    assert raw.maskEditor.current["shape"]["type"]=="linear"
    stroke("radial",(.2,.2),(.7,.8))
    assert raw.maskEditor.current["shape"]["type"]=="radial"
    stroke("brush",(.3,.3),(.6,.6))
    assert raw.maskEditor.current["shape"]["type"]=="raster"
    # Local slider must alter this mask rather than a global adjustment.
    slider=window.findChild(QObject,"maskField_amount_slider")
    scroll=window.findChild(QObject,"rawEditorScroll")
    flick=scroll.property("contentItem")
    y=slider.mapToItem(flick.property("contentItem"),QPointF()).y()
    flick.setProperty("contentY",max(0,y-100));pump(qapp,.1)
    point=slider.mapToScene(QPointF(slider.width()*.45,slider.height()/2)).toPoint()
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,point);pump(qapp,.1)
    assert raw.maskEditor.current["amount"]==pytest.approx(45,abs=2) and raw.values["exposure"]==0
    assert not [w for w in warnings if "RawMask" in w],warnings


def test_native_range_samples_preserve_adjustments_and_shift_adds(qapp):
    raw=RawEditor(memory=True);raw.open("sample.NEF");raw._full_size=[4000,3000]
    model=raw.maskEditor;model.arm("colorRange")
    result={"sampleRGB":[.2,.3,.4],"sampleDisplay":[.4,.5,.6],"sampleLuminance":[.3,.3]}
    sample={"kind":"colorRange","target":-1,"add":False}
    model.applySample(result,sample);model.adjust("exposure",1)
    assert model.current["shape"]["color"]==[.2,.3,.4] and model.sampleSwatch=="#668099"
    model.applySample(result,{**sample,"target":0,"add":True})
    assert len(model.current["parts"])==1 and model.current["adjust"]["exposure"]==1
    raw.undo();assert not model.current.get("parts")
    model.arm("luminanceRange")
    model.applySample(result,{"kind":"luminanceRange","target":model.selected,"add":False})
    assert model.current["shape"]["low"]==pytest.approx(.25)
    assert model.current["shape"]["high"]==pytest.approx(.35)
    assert masks_valid(model.rows)
    raw.shutdown()


def test_brush_density_caps_coverage(qapp):
    raw=RawEditor(memory=True);raw.open("sample.NEF");raw._full_size=[1200,800]
    model=raw.maskEditor;model.arm("brush");model.brushDynamics(50,30)
    model.stroke([[.5,.5]]*5)
    assert 0<decode_raster(model.current["shape"]).max()<=77
    raw.shutdown()


def test_component_composition_brush_preserves_base_and_history(qapp):
    from copy import deepcopy
    raw=RawEditor(memory=True);raw.open("sample.NEF");raw._full_size=[1200,800]
    model=raw.maskEditor
    model.arm("linear");model.draw(0,0,1,1);model.adjust("exposure",1)
    original=deepcopy(model.current["shape"])
    model.combineWith("subtract","brush");model.stroke([[.3,.3],[.5,.5]])
    assert len(model.rows)==1 and model.current["shape"]==original
    assert model.current["parts"][0]["op"]=="subtract" and model.current["adjust"]["exposure"]==1
    first=deepcopy(model.current["parts"][0]["shape"])
    model.stroke([[.7,.7]])
    assert model.current["shape"]==original and model.current["parts"][0]["shape"]!=first
    model.stop();model.selectComponent(1);model.componentAction("invert")
    assert model.current["parts"][0]["invert"]
    model.componentAction("duplicate");assert len(model.current["parts"])==2
    model.componentAction("intersect");assert model.current["parts"][0]["op"]=="intersect"
    model.componentAction("delete");assert len(model.current["parts"])==1
    raw.undo();assert len(model.current["parts"])==2
    assert masks_valid(model.rows)
    model.removeAll();assert not model.rows
    raw.undo();assert len(model.rows)==1
    raw.shutdown()


def test_overlay_modes_reuse_legacy_display_without_changing_recipe(qapp):
    from copy import deepcopy
    from PySide6.QtGui import QImage,QColor
    from image_triage.qml_shell.raw_preview import put_frame,frame_image
    raw=RawEditor(memory=True);raw.open("sample.NEF");raw._full_size=[100,100]
    base=QImage(100,100,QImage.Format_RGB32);base.fill(QColor("blue"))
    raw._preview=put_frame("overlay-test/base",base)
    model=raw.maskEditor;model.arm("linear");model.draw(0,0,1,0)
    recipe=deepcopy(raw._draft);pixels=[]
    for mode in ("color","color-bw","image-bw","image-black","image-white","white-black"):
        model.displayOption("mode",mode)
        image=frame_image(model.overlay)
        assert not image.isNull()
        pixels.append(image.pixelColor(50,50).rgba())
    assert len(set(pixels))>=5 and raw._draft==recipe
    model.displayOption("color","#00ff00");model.displayOption("opacity","25")
    assert model.overlayColor=="#00ff00" and model.overlayOpacity==25
    assert raw._draft==recipe
    raw.shutdown()


def test_floating_menus_trigger_real_component_and_overlay_actions(shell,qapp):
    window,raw,dock,_,warnings=prepare(shell,qapp)
    dock.setProperty("tool","Mask")
    model=raw.maskEditor;model.arm("linear");model.draw(0,0,1,1);pump(qapp,.2)
    def clickText(text):
        pending=[window.contentItem()]
        while pending:
            item=pending.pop()
            if item.isVisible() and item.property("text")==text:
                QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint())
                pump(qapp,.1);return
            pending.extend(item.childItems())
        raise AssertionError(text)
    clickText("Subtract");clickText("Brush")
    assert model.combining=="subtract" and model.tool=="brush"
    model.stroke([[.3,.3],[.5,.5]]);model.stop();pump(qapp,.1)
    assert model.current["parts"][0]["op"]=="subtract"
    button=window.findChild(QObject,"maskOverlayMenu")
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,button.mapToScene(QPointF(button.width()/2,button.height()/2)).toPoint());pump(qapp,.1)
    clickText("White on Black")
    assert model.overlayMode=="white-black"
    assert not [w for w in warnings if "RawMask" in w],warnings


def test_mask_adjustments_share_global_controls_and_numeric_editing(shell,qapp):
    window,raw,dock,_,warnings=prepare(shell,qapp)
    model=raw.maskEditor;model.arm("linear");model.draw(0,0,1,1)
    dock.setProperty("tool","Mask");pump(qapp,.2)
    def find(name):
        pending=[window.contentItem()]
        while pending:
            item=pending.pop()
            if item.objectName()==name:return item
            pending.extend(item.childItems())
    local=find("maskBasic_exposure")
    global_control=find("rawBasic_exposure")
    assert local is not None and global_control is not None
    for key in ("width","height","from","to","step","decimals","controlHeight"):
        assert local.property(key)==global_control.property(key),key
    value=find("maskBasic_exposure_value")
    scroll=window.findChild(QObject,"rawEditorScroll").property("contentItem")
    y=value.mapToItem(scroll.property("contentItem"),QPointF()).y()
    scroll.setProperty("contentY",max(0,y-80));pump(qapp,.1)
    QTest.mouseClick(window,Qt.LeftButton,Qt.NoModifier,value.mapToScene(QPointF(value.width()/2,value.height()/2)).toPoint())
    QTest.keyClick(window,Qt.Key_A,Qt.ControlModifier)
    for key in (Qt.Key_1,Qt.Key_Period,Qt.Key_2,Qt.Key_5):QTest.keyClick(window,key)
    QTest.keyClick(window,Qt.Key_Return);pump(qapp,.1)
    assert model.current["adjust"]["exposure"]==1.25 and raw.values["exposure"]==0
    assert not [w for w in warnings if "Raw" in w],warnings


def test_local_color_tools_round_trip_history_and_neutrality(qapp):
    from image_triage.camera_raw_recipe_masks import local_adjust_valid,masks_active,LOCAL_DEFAULTS
    raw=RawEditor(memory=True);raw.open("sample.NEF");raw._full_size=[4000,3000]
    model=raw.maskEditor;model.arm("linear");model.draw(0,0,1,1)
    assert not masks_active([{**model.current,"adjust":LOCAL_DEFAULTS}])
    model.adjust("vibrance",30)
    model.setCurve("pointCurve",[[0,0],[128,150],[255,255]])
    model.setMixer(2,"hslSat",20)
    model.setGrading("gradeShadows",240,25,-5)
    model.setCalibration(0,"calibHue",15)
    model.adjust("calibShadowTint",10)
    assert model.current["adjust"]["hslSat"][2]==20
    assert model.current["adjust"]["gradeShadows"]["hue"]==240
    assert model.current["adjust"]["calibHue"][0]==15
    assert masks_valid(model.rows) and masks_active(model.rows)
    assert raw.values["vibrance"]==0 and raw.values["pointCurve"]==[]
    raw.undo();assert model.current["adjust"]["calibShadowTint"]==0
    model.resetAdjustments();assert not masks_active(model.rows)
    for invalid in ({"pointCurve":[[0,0],[0,255]]},{"hslHue":[0]*7},{"gradeGlobal":{"sat":101}},{"curveSplits":[75,50,25]},{"gradeBlending":-1},{"sharpenAmount":20}):
        assert not local_adjust_valid(invalid)
    raw.shutdown()


def test_all_local_color_pages_load_shared_tools(shell,qapp):
    window,raw,dock,_,warnings=prepare(shell,qapp)
    raw.maskEditor.arm("linear");raw.maskEditor.draw(0,0,1,1)
    dock.setProperty("tool","Mask");pump(qapp,.1)
    def find(name):
        pending=[window.contentItem()]
        while pending:
            item=pending.pop()
            if item.objectName()==name:return item
            pending.extend(item.childItems())
        raise AssertionError(name)
    for title in ("Curve","Color Mixer","Color Grading","Calibration"):
        find("maskSection_"+title).setProperty("expanded",True);pump(qapp,.1)
    assert not [w for w in warnings if "Raw" in w],warnings
    assert raw.maskEditor.current["adjust"]["gradeBlending"]==50


def test_nothing_started_during_a_save_can_leave_the_editor_stuck_saving(qapp):
    # Selecting a range mask while "Saving edits..." showed moved the revision on; the save's result then counted as
    # stale and the editor refused open, cancel, done and every slider for the rest of the session.
    raw = RawEditor(memory=True); raw.open("sample.NEF")
    raw._saving = True
    revision = raw._revision
    raw._enqueue("preview")
    assert raw._revision == revision                         # refused: the save still owns the revision
    raw._revision += 1                                       # and even if something did move it on ...
    raw._arrived(revision, "save", "", "")
    assert raw.saving is False                               # ... the save being over is never missed
    raw.shutdown()
