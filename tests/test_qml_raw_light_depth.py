"""The Light Depth section: depth job, its settings, picking, undo, renders, saving, the legacy mask and the panel."""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtQml import QQmlComponent, QQmlEngine

from image_triage import light_depth, photocraft_bridge as bridge
from image_triage.qml_shell.raw_editor import RawEditor, _recipe, _without_light_depth
from test_qml_shell import qapp, pump  # noqa: F401


def wait(qapp, predicate):
    end = time.monotonic() + 5
    while not predicate() and time.monotonic() < end:
        pump(qapp, .02)
    assert predicate()


def ground(*_args):
    return np.repeat(np.linspace(0, 255, 60).astype(np.uint8)[:, None], 80, axis=1)


@pytest.fixture
def raw(qapp, monkeypatch):
    monkeypatch.setattr("image_triage.editor_tools.installed_models", lambda: frozenset(("depth",)))
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    image = QImage(80, 60, QImage.Format_RGBA8888)
    image.fill(QColor("gray"))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [4000, 3000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    raw.lightDepthEditor._runner = ground
    yield raw
    raw.shutdown()


def ready(raw, qapp):
    light = raw.lightDepthEditor
    light.prepare()
    wait(qapp, lambda: light.ready and not light.busy)
    return light


def test_first_adjustment_reads_depth_then_commits_only_the_settings(raw, qapp):
    light = raw.lightDepthEditor
    assert not light.ready and not light.active
    light.adjust("amount", 70)
    light.flush()          # the panel commits after a short pause; tests do not wait for it
    assert light.busy and "lightDepth" not in raw._draft
    wait(qapp, lambda: not light.busy)
    assert light.ready and light.active and light.shown
    assert raw._draft["lightDepth"]["amount"] == 70
    assert light.settings["amount"] == 70 and light.pad.startswith("image://rawpreview/thumb/")


def test_the_effect_never_becomes_a_mask_in_the_mask_tab(raw, qapp):
    light = raw.lightDepthEditor
    light.adjust("amount", 70)
    light.flush()
    wait(qapp, lambda: not light.busy)
    light.adjust("brightnessNear", 80)
    light.adjust("warmthFar", -40)
    light.flush()
    assert raw.maskEditor.rows == [] and not raw._draft.get("masks")
    assert raw._draft["lightDepth"]["brightnessNear"] == 80 and raw._draft["lightDepth"]["warmthFar"] == -40


def test_clicking_the_photo_picks_the_depth_under_the_point(raw, qapp):
    light = ready(raw, qapp)
    light.pick(0.5, 0.05)                                  # the dropper is not armed: nothing happens
    assert light.settings == light_depth.normalized(None)
    light.setPicking(True)
    assert light.picking
    light.pick(0.5, 0.95)
    assert not light.picking                               # one click, then the dropper is put away
    near = light.settings
    assert near["depth"] > 0.85 and (near["x"], near["y"]) == (0.5, 0.95)
    assert near["amount"] == 35 and raw._draft["lightDepth"]["depth"] == near["depth"]   # committed at once, and switched on
    light.setPicking(True)
    light.pick(0.5, 0.05)
    assert light.settings["depth"] < 0.15 and light.settings["amount"] == 35
    light.setOpen(True)
    light.setPicking(True)
    light.setOpen(False)                                   # closing the section puts the dropper away
    assert not light.picking


def test_picking_before_the_map_exists_waits_for_the_scene_depth(raw, qapp):
    light = raw.lightDepthEditor
    light.adjust("amount", 40)
    light.flush()          # the depth job starts
    light.setPicking(True)
    light.pick(0.25, 1.0)  # while it is still running
    wait(qapp, lambda: not light.busy)
    assert light.settings["depth"] > 0.9 and light.settings["amount"] == 40 and light.active


def test_the_position_moves_without_changing_the_depth(raw, qapp):
    light = ready(raw, qapp)
    light.adjust("depth", 0.3)
    light.flush()
    light.place(0.2, 0.35)
    light.flush()
    assert (light.settings["x"], light.settings["y"], light.settings["depth"]) == (0.2, 0.35, pytest.approx(0.3))
    light.place(5, -1)                                     # out of the photo: held at its edge
    light.flush()
    assert (light.settings["x"], light.settings["y"]) == (1.0, 0.0)


def test_a_drag_is_one_undo_step_and_undo_restores_the_sliders(raw, qapp):
    light = raw.lightDepthEditor
    light.adjust("amount", 30)
    light.flush()
    wait(qapp, lambda: not light.busy)
    steps = len(raw._undo)
    raw.beginGesture()
    for y in (0.2, 0.4, 0.6, 0.8):
        light.place(0.5, y)
        light.flush()
    raw.endGesture()
    assert len(raw._undo) == steps + 1
    raw.undo()
    assert light.settings["y"] == light_depth.DEFAULTS["y"] and light.settings["amount"] == 30
    raw.undo()
    assert not light.active and light.settings["amount"] == 0


def test_nothing_is_rendered_while_the_amount_is_zero(raw, qapp, monkeypatch):
    light = ready(raw, qapp)
    renders = []
    original = raw._draft_changed
    monkeypatch.setattr(raw, "_draft_changed", lambda: (renders.append(1), original())[1])
    light.adjust("warmth", 40)
    light.flush()
    assert renders == [] and light.settings["warmth"] == 40 and raw._draft["lightDepth"]["warmth"] == 40
    light.adjust("amount", 10)
    light.flush()
    assert renders == [1]
    light.adjust("warmth", 50)
    light.flush()
    assert renders == [1, 1]                               # once it does something, every change is rendered


def test_hide_show_and_remove(raw, qapp, monkeypatch):
    light = raw.lightDepthEditor
    light.adjust("amount", 60)
    light.flush()
    wait(qapp, lambda: not light.busy)
    light.toggleShown()
    assert not light.shown and light.active and light.settings["amount"] == 60     # off, with its settings kept
    assert not light_depth.active(raw._draft["lightDepth"])
    light.toggleShown()
    assert light.shown and light_depth.active(raw._draft["lightDepth"])
    light.remove()
    assert not light.active and "lightDepth" not in raw._draft and light.settings == light_depth.normalized(None)
    raw.undo()
    assert light.active and light.settings["amount"] == 60


def test_a_free_mask_is_needed_for_each_zone_the_effect_changes(raw, qapp):
    shape = {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}
    light = raw.lightDepthEditor
    raw._set_values({"masks": [{"name": f"M{i}", "shape": shape} for i in range(15)]}, "fill")
    light.adjust("amount", 60)
    light.flush()
    wait(qapp, lambda: not light.busy)
    assert len(raw.maskEditor.rows) == 15 and "16 masks" in light.error and not light.active     # near and far need two
    raw._set_values({"masks": [{"name": f"M{i}", "shape": shape} for i in range(14)]}, "fewer")
    light.adjust("amount", 60)
    light.flush()
    assert light.active and not light.error and len(raw.maskEditor.rows) == 14
    light.adjust("warmth", 30)                             # a third zone (the depth itself) would take a third mask
    light.flush()
    assert "3 of them" in light.error and light.settings["warmth"] == 0     # refused: the slider goes back


def test_missing_model_says_so_and_starts_nothing(raw, qapp, monkeypatch):
    monkeypatch.setattr("image_triage.editor_tools.installed_models", lambda: frozenset())
    light = raw.lightDepthEditor
    called = []
    light._runner = lambda *args: called.append(1)
    light.adjust("amount", 60)
    light.flush()
    pump(qapp, .05)
    assert not light.busy and not called and light.unavailable and light.error == light.unavailable


def test_cancel_and_photo_switch_never_apply_a_late_map(raw, qapp):
    light = raw.lightDepthEditor
    started, release = threading.Event(), threading.Event()

    def slow(*args):
        started.set()
        release.wait(3)
        return ground()

    light._runner = slow
    light.adjust("amount", 60)
    light.flush()
    wait(qapp, started.is_set)
    light.cancel()
    release.set()
    pump(qapp, .15)
    assert not light.busy and not light.ready and "lightDepth" not in raw._draft
    started.clear(); release.clear()
    light.adjust("amount", 60)
    light.flush()
    wait(qapp, started.is_set)
    raw.open("second.NEF")
    release.set()
    pump(qapp, .15)
    assert not light.busy and not light.ready and "lightDepth" not in raw._draft


def test_a_failed_depth_job_reports_and_can_be_retried(raw, qapp):
    light = raw.lightDepthEditor

    def broken(*args):
        raise RuntimeError("The mask engine stopped")

    light._runner = broken
    light.adjust("amount", 60)
    light.flush()
    wait(qapp, lambda: not light.busy)
    assert light.error == "The mask engine stopped" and not light.active
    light._runner = ground
    light.adjust("amount", 60)
    light.flush()
    wait(qapp, lambda: not light.busy)
    assert light.active and not light.error


def test_reset_keeps_the_effect_and_the_native_renderer_never_sees_its_settings(raw, qapp, tmp_path, monkeypatch):
    from image_triage.full_render import _without_host_settings

    light = raw.lightDepthEditor
    light.adjust("amount", 60)
    light.flush()
    wait(qapp, lambda: not light.busy)
    raw.adjust("contrast", 20)
    raw.reset()
    assert light.active and light.settings["amount"] == 60 and "contrast" not in raw._draft
    native = _without_host_settings({"version": 1, "cameraRaw": raw._draft})["cameraRaw"]
    assert "lightDepth" not in native and "masks" not in native
    assert "lightDepth" not in _without_light_depth(raw._draft)
    kept = tmp_path / "light_depth.png"
    from PIL import Image
    Image.fromarray(ground()).save(kept)
    monkeypatch.setattr("image_triage.depth_maps.light_depth_cache_file", lambda source, **kw: kept)
    built = _recipe(raw._draft, "sample.NEF")["cameraRaw"]
    assert [m["name"] for m in built["masks"]] == [light_depth.NEAR_NAME, light_depth.FAR_NAME] and "lightDepth" not in built
    assert "masks" not in raw._draft and raw.maskEditor.rows == []     # building them for a render leaves the draft alone


# --- what the native renderer is sent, what is saved ------------------------------------------------------------------------

@pytest.fixture
def lit_photo(tmp_path, monkeypatch):
    from PIL import Image
    from image_triage.raw_crop import publish

    photo = tmp_path / "photo.dng"
    photo.write_bytes(b"original RAW")
    initial = tmp_path / "initial.png"
    image = QImage(32, 24, QImage.Format_RGBA64)
    image.fill(QColor("red"))
    assert image.save(str(initial))
    original = {"exposure": .25}
    publish(str(photo), initial, original, bridge.file_signature(str(photo)))
    kept = tmp_path / "kept" / "light_depth.png"
    kept.parent.mkdir()
    Image.fromarray(ground()).save(kept)
    monkeypatch.setattr("image_triage.depth_maps.light_depth_cache_file", lambda source, **kw: kept)
    sent = []

    def launch(*args, write_root, **kwargs):
        def call(method, params):
            assert method == "app.render"
            sent.append(params["recipe"]["cameraRaw"])
            target = Path(write_root) / (params["out"] + ".png")
            assert image.save(str(target))
            return {"out": target.name}

        proc = SimpleNamespace(control=SimpleNamespace(full_render_supported=True, render_manifest_supported=True, call=call,
                                                       camera_raw_masks_supported=True),
                               path_within_root=lambda path: Path(path).name, stopped=False, is_running=lambda: False)
        proc.shutdown = lambda: setattr(proc, "stopped", True)
        return proc

    monkeypatch.setattr(bridge, "launch_photocraft", launch)
    return photo, original, sent


def test_previews_and_saves_build_the_masks_but_the_saved_recipe_holds_none(lit_photo, tmp_path):
    photo, original, sent = lit_photo
    light = light_depth.normalized({"amount": 60})
    draft = {"exposure": .25, "lightDepth": light}
    out = tmp_path / "preview"
    out.mkdir()
    RawEditor._work("preview", str(photo), original, draft, str(out))
    assert [m["name"] for m in sent[-1]["masks"]] == [light_depth.NEAR_NAME, light_depth.FAR_NAME] and "lightDepth" not in sent[-1]
    RawEditor._work("save", str(photo), original, draft, "")
    assert [m["name"] for m in sent[-1]["masks"]] == [light_depth.NEAR_NAME, light_depth.FAR_NAME]
    assert bridge.load_recipe(str(photo))["cameraRaw"] == {"exposure": .25}      # no masks, no host settings in the recipe
    assert light_depth.read_sidecar(bridge.recipe_path(str(photo)))[0] == light
    # Switched off or at Amount 0: nothing is sent to be rendered as a mask, and the settings file goes only at Amount 0.
    RawEditor._work("save", str(photo), {"exposure": .25}, {"exposure": .25, "lightDepth": {**light, "shown": 0}}, "")
    assert "masks" not in sent[-1] and light_depth.read_sidecar(bridge.recipe_path(str(photo)))[0]["shown"] == 0
    RawEditor._work("save", str(photo), {"exposure": .25}, {"exposure": .25}, "")
    assert not light_depth.sidecar_path(bridge.recipe_path(str(photo))).exists()


def test_a_save_without_the_depth_map_says_why_and_changes_nothing(lit_photo, monkeypatch):
    photo, original, sent = lit_photo
    before = bridge.recipe_path(str(photo)).read_bytes()
    monkeypatch.setattr("image_triage.depth_maps.light_depth_cache_file", lambda source, **kw: photo.parent / "gone.png")
    with pytest.raises(ValueError, match="depth map is missing"):
        RawEditor._work("save", str(photo), original, {"exposure": .25, "lightDepth": light_depth.normalized({"amount": 60})}, "")
    assert bridge.recipe_path(str(photo)).read_bytes() == before and sent == []


def recipe_with(photo, settings):
    path = bridge.recipe_path(str(photo))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": bridge.RECIPE_VERSION, "cameraRaw": settings}))
    return path


def test_loading_converts_or_drops_an_earlier_versions_mask_and_attaches_current_settings(tmp_path):
    photo = tmp_path / "IMG_1.NEF"
    photo.write_bytes(b"raw")
    legacy = {"name": "Light Depth", "shape": {"type": "raster", "w": 1, "h": 1, "data": "AAE="}}
    mine = {"name": "Window", "shape": {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}}
    recipe = recipe_with(photo, {"masks": [mine, legacy]})
    settings = RawEditor._work("load", str(photo), {}, {}, "")[0]
    assert settings["masks"] == [mine] and "lightDepth" not in settings          # dropped: its settings are unknown
    light_depth.sidecar_path(recipe).write_text(json.dumps({"version": 1, "settings": {
        "amount": 55, "x": .3, "y": .4, "depth": .3, "softness": 20, "warmth": 10, "reach": 60, "focus": 50}}))
    settings = RawEditor._work("load", str(photo), {}, {}, "")[0]
    assert settings["masks"] == [mine] and settings["lightDepth"]["amount"] == 55   # converted: the panel owns it now
    light_depth.write_sidecar(recipe, light_depth.normalized({"amount": 33}))
    recipe_with(photo, {})
    assert RawEditor._work("load", str(photo), {}, {}, "")[0]["lightDepth"]["amount"] == 33   # no mask needed any more


def test_saving_a_converted_photo_is_not_taken_for_a_changed_recipe(lit_photo):
    photo, original, sent = lit_photo
    legacy = {"name": "Light Depth", "shape": {"type": "raster", "w": 1, "h": 1, "data": "AAE="}}
    recipe_with(photo, {"exposure": .25, "masks": [legacy]})
    RawEditor._work("save", str(photo), original, {"exposure": .5}, "")        # the loaded draft had the mask removed
    assert bridge.load_recipe(str(photo))["cameraRaw"] == {"exposure": .5}


def test_a_stored_draft_with_an_earlier_mask_does_not_show_it_in_the_mask_tab(qapp):
    legacy = {"name": "Light Depth", "shape": {}}
    mine = {"name": "Window", "shape": {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}}
    raw = RawEditor(memory=True)
    try:
        raw.open("sample.NEF")
        # What loading hands over: the saved settings, the saved render, and a draft an earlier session left unsaved.
        raw._arrived(raw._revision, "load", ({"exposure": 1}, "", {"exposure": 1, "masks": [legacy, mine]}, ""), "")
        assert raw.loaded and [row["name"] for row in raw.maskEditor.rows] == ["Window"]
        assert [m["name"] for m in raw._draft["masks"]] == ["Window"]
    finally:
        raw.shutdown()


# --- the live preview helper (the path every slider drag takes) ---------------------------------------------------------------

def preview_worker(monkeypatch, tmp_path, with_map=True):
    from PIL import Image
    from image_triage.qml_shell import raw_preview

    photo = tmp_path / "sample.NEF"
    photo.write_bytes(b"raw")
    kept = tmp_path / "kept.png"
    if with_map:
        Image.fromarray(ground()).save(kept)
    monkeypatch.setattr("image_triage.depth_maps.light_depth_cache_file", lambda source, **kw: kept)
    calls = []

    class Connection:
        def __init__(self, exe, own):
            own(self)

        def call(self, request):
            calls.append(request)
            image = QImage(40, 30, QImage.Format_RGBA8888)
            image.fill(QColor("gray"))
            return ({"histogram": [[1200] + [0] * 255] * 3, "fullSize": [8000, 6000]}, image)

        def close(self):
            pass

        def interrupt(self):
            pass

    monkeypatch.setattr(raw_preview, "Connection", Connection)
    worker = raw_preview.PreviewWorker("worker.exe")
    done, errors = [], []
    worker.completed.connect(lambda *args: done.append(args))
    worker.failed.connect(lambda *args: errors.append(args))
    return photo, worker, calls, done, errors


def test_the_live_preview_gets_light_depths_masks_for_the_draft_and_for_the_saved_picture(qapp, monkeypatch, tmp_path):
    photo, worker, calls, done, errors = preview_worker(monkeypatch, tmp_path)
    saved = light_depth.normalized({"amount": 30})
    draft = {"lightDepth": light_depth.normalized({"amount": 60})}
    worker.submit(1, str(photo), {"lightDepth": saved}, draft)       # the saved edit already has the effect
    wait(qapp, lambda: done or errors)
    assert not errors
    names = [[m["name"] for m in call["recipe"]["cameraRaw"].get("masks", [])] for call in calls]
    both = [light_depth.NEAR_NAME, light_depth.FAR_NAME]
    assert [call["op"] for call in calls] == ["open", "render"] and names == [both, both]
    assert all("lightDepth" not in call["recipe"]["cameraRaw"] for call in calls)
    exposure = [call["recipe"]["cameraRaw"]["masks"][0]["adjust"]["exposure"] for call in calls]
    assert exposure[0] != exposure[1]                                 # the baseline is the saved effect, the render the draft's
    worker.close()


def test_the_live_preview_without_the_effect_or_without_the_map(qapp, monkeypatch, tmp_path):
    photo, worker, calls, done, errors = preview_worker(monkeypatch, tmp_path, with_map=False)
    worker.submit(1, str(photo), {}, {"exposure": .5, "lightDepth": light_depth.normalized({"amount": 0})})
    wait(qapp, lambda: done or errors)
    assert not errors and "masks" not in calls[-1]["recipe"]["cameraRaw"]          # Amount 0 changes nothing and needs no map
    worker.submit(2, str(photo), {}, {"lightDepth": light_depth.normalized({"amount": 40})})
    wait(qapp, lambda: errors)
    assert "depth map is missing" in errors[-1][1]
    worker.close()


# --- the panel and the handles on the photo -------------------------------------------------------------------------------

def make_item(raw, source):
    import image_triage.qml_shell as shell
    from image_triage.qml_shell import raw_preview
    from image_triage.qml_shell.theme import Theme

    engine = QQmlEngine()
    raw_preview.install_images(engine)
    engine.rootContext().setContextProperty("theme", Theme())
    engine.rootContext().setContextProperty("rawModel", raw)
    component = QQmlComponent(engine)
    component.setData(source, QUrl.fromLocalFile(str(Path(shell.__file__).parent / "qml" / "LightDepthHarness.qml")))
    item = component.create()
    assert item is not None, component.errors()
    return item, (engine, component)    # the caller keeps these alive as long as the item


PANEL = b'import QtQuick\nItem { width: 320; height: 900\n RawLightDepth { width: 300; model: rawModel.lightDepthEditor; raw: rawModel } }'


def find_item(root, name):
    """An item by object name, found through the visual tree (items a Repeater makes are not QObject children of the panel)."""
    if root.objectName() == name:
        return root
    for child in root.childItems():
        found = find_item(child, name)
        if found is not None:
            return found
    return None


def test_the_section_renders_with_all_its_controls_and_drives_the_settings(raw, qapp):
    item, keep = make_item(raw, PANEL)
    try:
        light = raw.lightDepthEditor
        assert light.open                                  # the handles on the photo follow the section being open
        wait(qapp, lambda: light.ready)
        for name in ("lightDepth_amount", "lightDepth_depth", "lightDepthPick", "lightDepthPad", "lightDepth_softness",
                     "lightDepth_warmth", "lightDepthAdvanced", "lightDepthShown", "lightDepthRemove"):
            assert item.findChild(QObject, name) is not None, name
        section = item.childItems()[0]
        section.setProperty("advanced", True)
        pump(qapp, .05)
        for name in ("brightnessNear", "warmthNear", "brightnessFar", "warmthFar", "size", "wrap"):
            assert find_item(item, "lightDepth_" + name) is not None, name
        for name in ("small", "base", "large"):
            assert find_item(item, "lightDepthModel_" + name) is not None, name       # the depth model choice
        assert find_item(item, "lightDepthModelInfo") is not None
        slider = item.findChild(QObject, "lightDepth_amount_slider")
        slider.setProperty("value", 45)
        slider.moved.emit()
        light.flush()
        assert light.settings["amount"] == 45 and light.active and raw._draft["lightDepth"]["amount"] == 45
        depth = item.findChild(QObject, "lightDepth_depth_slider")
        depth.setProperty("value", 30)                     # Depth 30 is nearer the camera than 70: the split sits at 0.7
        depth.moved.emit()
        light.flush()
        assert light.settings["depth"] == pytest.approx(0.7)
        far = find_item(item, "lightDepth_brightnessFar_slider")
        far.setProperty("value", 90)
        far.moved.emit()
        light.flush()
        assert raw._draft["lightDepth"]["brightnessFar"] == 90
        pad = item.findChild(QObject, "lightDepthPad")
        assert pad.property("width") == 300 and pad.property("height") == 225
        button = item.findChild(QObject, "lightDepthPick")
        from PySide6.QtCore import QMetaObject
        QMetaObject.invokeMethod(button, "toggle")
        assert light.picking
        QMetaObject.invokeMethod(button, "toggle")
        assert not light.picking
    finally:
        item.deleteLater()
        pump(qapp, .05)
    assert not raw.lightDepthEditor.open                   # closing the section takes the handles off the photo


HANDLES = (b'import QtQuick\nItem { width: 600; height: 400\n RawLightDepthHandles { objectName: "handles"; x: 0; y: 0; width: 600; height: 400;'
           b' model: rawModel.lightDepthEditor; raw: rawModel } }')


def test_the_handles_on_the_photo_move_the_position_and_set_the_size_and_wrap(raw, qapp):
    light = ready(raw, qapp)
    light.adjust("amount", 50)
    light.adjust("size", 40)
    light.flush()
    item, keep = make_item(raw, HANDLES)
    try:
        handles = item.findChild(QObject, "handles")
        assert not handles.property("visible")             # not while the section is closed
        light.setOpen(True)
        pump(qapp, .05)
        assert handles.property("visible")
        position, size, wrap = (item.findChild(QObject, name) for name in ("lightDepthPosition", "lightDepthSize", "lightDepthWrap"))
        assert item.findChild(QObject, "lightDepthRing").property("visible")             # a pool: size below 100
        centre = (position.property("x") + position.property("width") / 2, position.property("y") + position.property("height") / 2)
        assert centre == pytest.approx((300, 240))        # the default position, 0.5 and 0.6 of the picture
        position.moved.emit(150.0, 100.0)
        light.flush()
        assert (light.settings["x"], light.settings["y"]) == (pytest.approx(0.25), pytest.approx(0.25))
        before = light.settings["size"]
        size.moved.emit(150.0 + 200.0, 100.0)              # dragged 200 pixels from the centre: half of 600 wide is 300
        light.flush()
        assert light.settings["size"] != before and light.settings["size"] == pytest.approx((200 / 300 - .05) / .95 * 100)
        wrap.moved.emit(item.findChild(QObject, "lightDepthWrapTrack").property("x") + 1000, 380.0)   # past the end of its track
        light.flush()
        assert light.settings["wrap"] == 100
        light.adjust("size", 100)                          # the whole photo: no ring
        light.flush()
        pump(qapp, .05)
        assert not item.findChild(QObject, "lightDepthRing").property("visible")
        light.toggleShown()                                # switched off: the handles leave the photo
        pump(qapp, .05)
        assert not handles.property("visible")
    finally:
        item.deleteLater()
        pump(qapp, .05)


def test_no_preview_yet_waits_instead_of_failing_and_goes_on_when_it_arrives(qapp, monkeypatch):
    monkeypatch.setattr("image_triage.editor_tools.installed_models", lambda: frozenset(("depth",)))
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    light = raw.lightDepthEditor
    light._runner = ground
    try:
        light.adjust("amount", 60)                        # asked for before any preview has been developed
        light.flush()
        assert light.waiting and not light.busy and light.error == ""
        assert "RAW preview" in light.message and light.settings["amount"] != 60
        image = QImage(80, 60, QImage.Format_RGBA8888)
        image.fill(QColor("gray"))
        frame = ({"histogram": [[0] * 256] * 3, "fullSize": [4000, 3000]}, image)
        raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))     # the preview arrives
        wait(qapp, lambda: light.ready and not light.busy)
        assert not light.waiting and light.active and light.settings["amount"] == 60   # and what was asked for is applied
    finally:
        raw.shutdown()


def test_a_preview_that_failed_is_named_not_waited_for_silently(qapp, monkeypatch):
    monkeypatch.setattr("image_triage.editor_tools.installed_models", lambda: frozenset(("depth",)))
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    try:
        raw._error = "PhotoCraft is not installed"
        raw.lightDepthEditor.prepare()
        assert raw.lightDepthEditor.waiting and "PhotoCraft is not installed" in raw.lightDepthEditor.message
    finally:
        raw.shutdown()


def test_an_idle_panel_offers_to_create_the_depth_map(raw, qapp):
    item, keep = make_item(raw, PANEL)
    try:
        light = raw.lightDepthEditor
        wait(qapp, lambda: light.ready)
        button = item.findChild(QObject, "lightDepthCreate")
        assert button is not None and not button.property("visible")          # a depth map exists: no button
        light._maps.clear()                                                    # as after a cancel, or when it never started
        light._message = "Cancelled"
        light.changed.emit()
        pump(qapp, .1)
        assert button.property("visible")
        light.prepare()                                                        # what the button does
        wait(qapp, lambda: light.ready)
        pump(qapp, .1)
        assert not button.property("visible")
    finally:
        item.deleteLater()
        pump(qapp, .05)


def test_the_first_placement_switches_the_effect_on(raw, qapp):
    light = ready(raw, qapp)
    assert light.settings["amount"] == 0 and not light.active
    light.place(0.4, 0.7)
    light.flush()
    assert light.settings["amount"] == 35 and light.active                 # Amount 0 would have added nothing to the photo
    light.adjust("amount", 0)
    light.flush()
    light.place(0.5, 0.5)                                                  # once there are settings, placing leaves Amount alone
    light.flush()
    assert light.settings["amount"] == 0


def test_a_run_of_slider_moves_commits_far_less_often(raw, qapp, monkeypatch):
    light = raw.lightDepthEditor
    light.adjust("amount", 20)
    light.flush()
    wait(qapp, lambda: light.ready and not light.busy)
    commits = []
    original = raw._set_values
    monkeypatch.setattr(raw, "_set_values", lambda *a, **k: (commits.append(1), original(*a, **k))[1])
    for value in range(21, 61):                         # forty slider events in a burst
        light.adjust("amount", value)
    assert light.settings["amount"] == 60 and commits == []      # shown at once, not yet committed
    wait(qapp, lambda: raw._draft["lightDepth"]["amount"] == 60)
    assert len(commits) == 1                            # one commit for the whole burst


# --- the choice of depth model -----------------------------------------------------------------------------------------------

def counting_runner(calls):
    def run(image, path, progress, cancel, model=0):
        calls.append(model)
        return ground(), {"model": light_depth.DEPTH_MODELS[model], "cached": False, "seconds": 1.5, "load": 0.5, "inference": 1.0}
    return run


def test_each_depth_model_makes_its_own_map_and_switching_back_needs_no_new_one(raw, qapp):
    light = raw.lightDepthEditor
    calls = []
    light._runner = counting_runner(calls)
    light._installed = lambda key: True
    light.adjust("amount", 60)
    light.flush()
    wait(qapp, lambda: not light.busy and light.ready)
    assert calls == [0] and light.selectedModel == 0 and light.modelInfo.startswith("Small depth model: made in 1.5 s")
    light.setModel(1)                                       # the picture keeps its settings; the Base model reads the scene
    assert light.selectedModel == 1 and light.busy and not light.ready
    wait(qapp, lambda: not light.busy)
    assert calls == [0, 1] and light.ready and raw._draft["lightDepth"]["depthModel"] == 1
    assert light.modelInfo == "Base depth model: made in 1.5 s (0.5 s loading the model, 1.0 s estimating)"
    light.setModel(0)
    assert calls == [0, 1] and light.ready and raw._draft["lightDepth"]["depthModel"] == 0     # the first map is still there
    assert [m["key"] for m in light.models] == ["small", "base", "large"] and all(m["installed"] for m in light.models)


def test_a_model_that_is_not_installed_is_named_and_nothing_starts(raw, qapp):
    light = raw.lightDepthEditor
    calls = []
    light._runner = counting_runner(calls)
    light._installed = lambda key: key != "large"
    light.setModel(2)
    light.prepare()
    assert calls == [] and not light.busy
    assert light.unavailable == "The Large depth model is not installed." and light.error == light.unavailable
    assert [m["installed"] for m in light.models] == [True, True, False]
    light.setModel(0)                                       # back to one that is: the reason goes away
    light.refresh()
    assert light.unavailable == ""
