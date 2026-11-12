"""AI services, cancellation, source identity, and normalized SAM prompts in the QML editor."""
import threading
import time

import numpy as np
import pytest
from PySide6.QtGui import QImage, QColor
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from image_triage.qml_shell.raw_editor import RawEditor
from image_triage.qml_shell.raw_masks import encode_raster
from image_triage.qml_shell.raw_mask_ai import read_coverage, run_mask_job
from test_qml_shell import qapp, pump  # noqa: F401
from test_qml_shell_viewer import shell  # noqa: F401
from test_qml_raw_crop import prepare


def wait(qapp, predicate):
    end=time.monotonic()+5
    while not predicate() and time.monotonic()<end: pump(qapp,.02)
    assert predicate()


def choice(kind="subject", value=255):
    return {"id":kind,"label":kind.title(),"shape":encode_raster(np.full((30,40),value,dtype=np.uint8)),"coverage":.5,"invert":kind=="background"}


@pytest.fixture
def raw(qapp,monkeypatch):
    monkeypatch.setattr("image_triage.editor_tools.installed_models",lambda:frozenset(("birefnet","sam","oneformer")))
    raw=RawEditor(memory=True);raw.open("sample.NEF")
    image=QImage(40,30,QImage.Format_RGBA8888);image.fill(QColor("blue"))
    frame=({"histogram":[[0]*256]*3,"fullSize":[4000,3000]},image)
    raw._proxy_arrived(raw._revision,(frame,frame,0,"sample.NEF"))
    yield raw
    raw.shutdown()


def test_subject_background_and_scene_choices(raw,qapp):
    ai=raw.maskEditor.ai
    ai._runner=lambda kind,*args:[choice(kind)]
    ai.start("subject");wait(qapp,lambda:not ai.busy)
    assert len(raw.maskEditor.rows)==1 and not raw.maskEditor.current["invert"]
    ai.start("background");wait(qapp,lambda:not ai.busy)
    assert raw.maskEditor.current["invert"]
    raw.undo();assert len(raw.maskEditor.rows)==1  # Including inversion: one undo step.
    ai._runner=lambda *args:[choice("vegetation")]
    ai.start("scene");wait(qapp,lambda:not ai.busy)
    assert ai.selectionRequired and len(ai.choices)==1 and len(raw.maskEditor.rows)==1
    ai.choose(0);assert len(raw.maskEditor.rows)==2 and "Vegetation" in ai.message


def test_cancel_and_photo_switch_never_publish_late_masks(raw,qapp):
    ai=raw.maskEditor.ai;started=threading.Event();release=threading.Event()
    def runner(*args):
        started.set();release.wait(3);return [choice()]
    ai._runner=runner
    ai.start("subject");wait(qapp,started.is_set)
    ai.cancel();release.set();wait(qapp,lambda:not ai.busy)
    assert raw.maskEditor.rows==[] and ai.message=="Cancelled"
    started.clear();release.clear();ai.start("subject");wait(qapp,started.is_set)
    raw.open("second.NEF");release.set();pump(qapp,.15)
    assert raw.maskEditor.rows==[] and not ai.busy


def test_sam_crop_prompts_refine_same_mask_preserving_adjustments(raw,qapp):
    ai=raw.maskEditor.ai;calls=[]
    def runner(kind,source,root,points,labels,*args):
        calls.append((source,list(points),list(labels)))
        return [choice("object",255 if len(points)==1 else 128)]
    ai._runner=runner
    raw.setCrop(.2,.1,.5,.6)
    ai.start("object")
    ai.click(.1,.2,True)
    assert not ai.busy and "first" in ai.error
    ai.click(.1,.2,False);wait(qapp,lambda:not ai.busy)
    assert len(raw.maskEditor.rows)==1
    first=raw.maskEditor.current["shape"]
    raw.maskEditor.adjust("exposure",1.25)
    ai.click(.8,.7,True);wait(qapp,lambda:not ai.busy)
    assert len(raw.maskEditor.rows)==1 and raw.maskEditor.current["adjust"]["exposure"]==1.25
    assert calls[0][0]==calls[1][0]
    assert calls[0][1][0]==pytest.approx((.25,.22))
    assert calls[1][2]==[1,0] and len(ai.points)==2
    raw.undo();assert raw.maskEditor.current["shape"]==first


def test_missing_models_and_no_detection_do_not_add_masks(raw,qapp,monkeypatch):
    ai=raw.maskEditor.ai
    monkeypatch.setattr("image_triage.editor_tools.installed_models",lambda:frozenset())
    ai.start("subject")
    assert not ai.busy and "Subject masks" in ai.error
    monkeypatch.setattr("image_triage.editor_tools.installed_models",lambda:frozenset(("oneformer",)))
    ai._runner=lambda *args:[]
    ai.start("sky");wait(qapp,lambda:not ai.busy)
    assert "No sky" in ai.message and raw.maskEditor.rows==[]


def test_pending_scene_selection_rejects_changed_source(raw,qapp,monkeypatch):
    fingerprint=["before"]
    monkeypatch.setattr("image_triage.qml_shell.raw_mask_ai.bridge.file_signature",lambda path:fingerprint[0])
    ai=raw.maskEditor.ai;ai._runner=lambda *args:[choice("vegetation")]
    ai.start("scene");wait(qapp,lambda:not ai.busy)
    fingerprint[0]="after"
    ai.choose(0)
    assert raw.maskEditor.rows==[] and "changed" in ai.error


def test_landscape_explicit_combined_selection_and_source_cache(raw,qapp,monkeypatch):
    fingerprint=["one"];calls=[]
    monkeypatch.setattr("image_triage.qml_shell.raw_mask_ai.bridge.file_signature",lambda path:fingerprint[0])
    ai=raw.maskEditor.ai
    def runner(*args):
        calls.append(1);return [choice("sky"),choice("mountains",128)]
    ai._runner=runner;ai.start("scene");wait(qapp,lambda:not ai.busy)
    assert ai.optionsOpen and raw.maskEditor.rows==[]
    ai.closeOptions();assert raw.maskEditor.rows==[]
    ai.start("scene");assert len(calls)==1
    ai.createSelection([0,1])
    assert len(raw.maskEditor.rows)==1 and len(raw.maskEditor.current["parts"])==1
    assert not ai.optionsOpen
    fingerprint[0]="two";ai.start("scene");wait(qapp,lambda:not ai.busy)
    assert len(calls)==2


def test_background_people_cached_and_stale_results_discarded(raw,qapp,monkeypatch):
    from image_triage.qml_shell import mask_people_cache as cache
    fingerprint=["one"];calls=[];saved=[]
    monkeypatch.setattr("image_triage.qml_shell.raw_mask_ai.bridge.file_signature",lambda path:fingerprint[0])
    monkeypatch.setattr(cache,"load",lambda *args:None)
    monkeypatch.setattr(cache,"save",lambda *args:saved.append(args))
    monkeypatch.setattr(cache,"culling_faces",lambda path:2)
    ai=raw.maskEditor.ai;ai._auto_people=True
    ai._runner=lambda *args:calls.append(1) or [choice("person")]
    ai.openMasking();wait(qapp,lambda:ai.peopleCount==1)
    assert not ai.busy and len(saved)==1 and raw.maskEditor.rows==[]
    ai.closeMasking();ai.openMasking();pump(qapp,.1)
    assert len(calls)==1
    ai.start("people");assert ai.optionsOpen
    ai.createSelection([0]);assert len(raw.maskEditor.rows)==1
    # A changed source cannot publish a pending background detection.
    ai._people_signature="one";ai._people_scanning=True;fingerprint[0]="two"
    ai._people_arrived(ai._people_ticket,[choice("person")],"")
    assert ai.peopleCount==0 and "changed" in ai.peopleMessage


def test_ai_intersection_combines_into_current_mask(raw,qapp):
    mask=raw.maskEditor;ai=mask.ai
    mask.arm("linear");mask.draw(0,0,1,1)
    ai._runner=lambda *args:[choice("sky")]
    mask.combineWith("intersect","sky");wait(qapp,lambda:not ai.busy)
    assert len(mask.rows)==1 and mask.current["shape"]["type"]=="linear"
    assert mask.current["parts"][0]["op"]=="intersect"
    raw.undo();assert not mask.current.get("parts")


def test_people_disk_cache_empty_and_source_versions(tmp_path,monkeypatch):
    from image_triage.qml_shell import mask_people_cache as cache
    monkeypatch.setattr("image_triage.ai_paths.managed_cache_dir",lambda name:tmp_path)
    cache.save("photo.NEF","one",[])
    assert cache.load("photo.NEF","one")==[]
    assert cache.load("photo.NEF","two") is None
    cache.save("photo.NEF","two",[choice("person")])
    assert len(cache.load("photo.NEF","two"))==1


def test_coverage_validation_and_existing_subject_service(tmp_path,monkeypatch):
    from PIL import Image
    source=tmp_path/"source.png";Image.new("RGB",(40,30),"blue").save(source)
    called=[]
    def subject(src,out,progress,cancel):
        called.append(src);Image.new("L",(40,30),255).save(out)
    monkeypatch.setattr("image_triage.scene_mask_jobs.run_subject_mask",subject)
    results=run_mask_job("background",source,tmp_path,[],[],lambda msg:None,threading.Event())
    assert called==[source] and results[0]["invert"]
    bad=tmp_path/"wrong.png";Image.new("L",(100,10)).save(bad)
    with pytest.raises(ValueError,match="proportions"):read_coverage(bad,4/3)


def test_qml_sam_prompts_and_single_scene_choice(shell,qapp,monkeypatch):
    monkeypatch.setattr("image_triage.editor_tools.installed_models",lambda:frozenset(("birefnet","sam","oneformer")))
    window,raw,dock,_,warnings=prepare(shell,qapp)
    dock.setProperty("tool","Mask");pump(qapp,.1)
    ai=raw.maskEditor.ai;calls=[]
    def runner(kind,source,root,points,labels,*args):
        calls.append((kind,points,labels))
        return [choice("vegetation" if kind=="scene" else "object",255 if len(points)<2 else 128)]
    ai._runner=runner
    def find(name):
        pending=[window.contentItem()]
        while pending:
            item=pending.pop()
            if item.objectName()==name:return item
            pending.extend(item.childItems())
        raise AssertionError(name)
    def click(item,fx=.5,fy=.5,modifiers=Qt.NoModifier):
        QTest.mouseClick(window,Qt.LeftButton,modifiers,item.mapToScene(QPointF(item.width()*fx,item.height()*fy)).toPoint())
        pump(qapp,.05)
    click(find("aiMaskTool_object"))
    overlay=window.findChild(QObject,"rawMaskOverlay")
    assert raw.maskEditor.tool=="object"
    click(overlay,.3,.4);wait(qapp,lambda:not ai.busy)
    click(overlay,.6,.5,Qt.ControlModifier);wait(qapp,lambda:not ai.busy)
    assert calls[-1][2]==[1,0] and len(raw.maskEditor.rows)==1
    click(find("newFloatingMask"))
    click(find("aiMaskTool_scene"));wait(qapp,lambda:not ai.busy)
    pump(qapp,.15)
    action=find("aiMaskChoice_0")
    assert action is not None and action.isVisible()
    options=find("maskOptionsPane")
    pane_position=options.mapToScene(QPointF())
    assert pane_position.x()>=dock.x()
    assert pane_position.x()+options.width()<=dock.x()+dock.width()
    for button in (Qt.MiddleButton,Qt.RightButton):
        QTest.mouseClick(window,button,Qt.NoModifier,action.mapToScene(QPointF(5,5)).toPoint())
        pump(qapp,.04)
        assert ai.optionsOpen and raw.maskEditor.rows==raw.values["masks"]
    click(action)
    assert len(raw.maskEditor.rows)==1
    click(find("createMaskSelection"))
    assert len(raw.maskEditor.rows)==2 and "Vegetation" in raw.maskEditor.current["name"]
    assert not [w for w in warnings if "RawMask" in w],warnings

@pytest.mark.parametrize('portrait', [False, True])
def test_scene_mask_keeps_supported_fine_detail_through_recipe(tmp_path, monkeypatch, portrait):
    from types import SimpleNamespace
    from PIL import Image
    from image_triage.qml_shell.raw_masks import decode_raster
    from image_triage.camera_raw_recipe_masks import masks_valid

    # A model result already within the native limit must survive byte-for-byte.
    # Reducing it to 1024 loses much of the isolated thin feature before export.
    values = np.zeros((1024, 1536), dtype=np.uint8)
    values[:, :768] = 255
    values[:, 1000] = 255
    if portrait:
        values = values.T.copy()
    model_mask = tmp_path / 'model-sky.png'
    Image.fromarray(values).save(model_mask)
    source = tmp_path / 'source.png'
    Image.new('RGB', (values.shape[1], values.shape[0]), 'grey').save(source)
    monkeypatch.setattr('image_triage.semantic_masks.ensure_semantic_masks',
                        lambda *a, **kw: SimpleNamespace(mask_paths={'sky': model_mask}))
    results = run_mask_job('scene', source, tmp_path, [], [], lambda msg: None, threading.Event())
    assert len(results) == 1
    shape = results[0]['shape']
    assert masks_valid([{'shape': shape, 'adjust': {'exposure': 1}}])
    assert np.array_equal(decode_raster(shape), values)
