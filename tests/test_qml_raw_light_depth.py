"""The Light Depth section: depth job, the mask it manages, undo, saving, and the panel."""
import threading
import time

import numpy as np
import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage

from image_triage import light_depth
from image_triage.camera_raw_recipe_masks import masks_valid
from image_triage.qml_shell.raw_editor import RawEditor, _without_light_depth
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


def test_first_adjustment_reads_depth_then_adds_one_managed_mask(raw, qapp):
    light = raw.lightDepthEditor
    assert not light.ready and not light.active
    light.adjust("amount", 70)
    assert light.busy and raw.maskEditor.rows == []
    wait(qapp, lambda: not light.busy)
    rows = raw.maskEditor.rows
    assert light.ready and light.active and masks_valid(rows)
    assert [row["name"] for row in rows] == ["Light Depth"]
    assert rows[0]["adjust"] == {"exposure": pytest.approx(1.4)}
    assert light.settings["amount"] == 70 and light.pad.startswith("image://rawpreview/thumb/")


def test_placing_the_light_takes_the_distance_under_the_handle(raw, qapp):
    light = raw.lightDepthEditor
    light.adjust("amount", 50)
    wait(qapp, lambda: not light.busy)
    light.place(0.5, 0.95)
    near = light.settings
    assert near["depth"] > 0.85 and (near["x"], near["y"]) == (0.5, 0.95)
    light.place(0.5, 0.05)
    assert light.settings["depth"] < 0.15
    assert len(raw.maskEditor.rows) == 1


def test_placing_before_the_map_exists_waits_for_the_scene_depth(raw, qapp):
    light = raw.lightDepthEditor
    light.adjust("amount", 40)
    light.place(0.25, 1.0)  # while the depth job is still running
    wait(qapp, lambda: not light.busy)
    assert light.settings["depth"] > 0.9 and light.settings["amount"] == 40 and light.active


def test_a_drag_is_one_undo_step_and_undo_restores_the_sliders(raw, qapp):
    light = raw.lightDepthEditor
    light.adjust("amount", 30)
    wait(qapp, lambda: not light.busy)
    steps = len(raw._undo)
    raw.beginGesture()
    for y in (0.2, 0.4, 0.6, 0.8):
        light.place(0.5, y)
    raw.endGesture()
    assert len(raw._undo) == steps + 1
    raw.undo()
    assert light.settings["y"] == light_depth.DEFAULTS["y"] and light.settings["amount"] == 30
    raw.undo()
    assert not light.active and light.settings["amount"] == 0


def test_no_mask_is_added_while_the_amount_is_zero(raw, qapp):
    light = raw.lightDepthEditor
    light.adjust("warmth", 40)
    wait(qapp, lambda: not light.busy)
    assert raw.maskEditor.rows == [] and light.settings["warmth"] == 40
    light.adjust("amount", 10)
    assert raw.maskEditor.rows[0]["adjust"] == {"exposure": pytest.approx(0.2), "temperature": pytest.approx(24)}


def test_hide_show_and_remove(raw, qapp):
    light = raw.lightDepthEditor
    light.adjust("amount", 60)
    wait(qapp, lambda: not light.busy)
    light.toggleShown()
    assert not light.shown and light.active and raw.maskEditor.rows[0]["enabled"] is False
    light.toggleShown()
    assert light.shown
    light.remove()
    assert not light.active and raw.maskEditor.rows == [] and light.settings == light_depth.normalized(None)
    raw.undo()
    assert light.active and light.settings["amount"] == 60


def test_sixteen_masks_refuse_a_seventeenth_with_a_reason(raw, qapp):
    shape = {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}
    raw._set_values({"masks": [{"name": f"M{i}", "shape": shape} for i in range(16)]}, "fill")
    light = raw.lightDepthEditor
    light.adjust("amount", 60)
    wait(qapp, lambda: not light.busy)
    assert len(raw.maskEditor.rows) == 16 and "16 masks" in light.error and not light.active


def test_missing_model_says_so_and_starts_nothing(raw, qapp, monkeypatch):
    monkeypatch.setattr("image_triage.editor_tools.installed_models", lambda: frozenset())
    light = raw.lightDepthEditor
    called = []
    light._runner = lambda *args: called.append(1)
    light.adjust("amount", 60)
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
    wait(qapp, started.is_set)
    light.cancel()
    release.set()
    pump(qapp, .15)
    assert not light.busy and not light.ready and raw.maskEditor.rows == []
    started.clear(); release.clear()
    light.adjust("amount", 60)
    wait(qapp, started.is_set)
    raw.open("second.NEF")
    release.set()
    pump(qapp, .15)
    assert not light.busy and not light.ready and raw.maskEditor.rows == []


def test_a_failed_depth_job_reports_and_can_be_retried(raw, qapp):
    light = raw.lightDepthEditor

    def broken(*args):
        raise RuntimeError("The mask engine stopped")

    light._runner = broken
    light.adjust("amount", 60)
    wait(qapp, lambda: not light.busy)
    assert light.error == "The mask engine stopped" and not light.active
    light._runner = ground
    light.adjust("amount", 60)
    wait(qapp, lambda: not light.busy)
    assert light.active and not light.error


def test_reset_keeps_the_light_like_other_masks_and_native_never_sees_its_settings(raw, qapp):
    from image_triage.full_render import _without_host_settings

    light = raw.lightDepthEditor
    light.adjust("amount", 60)
    wait(qapp, lambda: not light.busy)
    raw.adjust("contrast", 20)
    raw.reset()
    assert light.active and light.settings["amount"] == 60 and "contrast" not in raw._draft
    native = _without_host_settings({"version": 1, "cameraRaw": raw._draft})["cameraRaw"]
    assert "lightDepth" not in native and native["masks"][0]["name"] == "Light Depth"
    assert "lightDepth" not in _without_light_depth(raw._draft)


def test_saving_writes_the_settings_beside_the_recipe_and_loading_reads_them(tmp_path, monkeypatch):
    from image_triage import photocraft_bridge as bridge
    from image_triage.qml_shell.raw_masks import encode_raster

    photo = tmp_path / "IMG_1.NEF"
    photo.write_bytes(b"raw")
    recipe = bridge.recipe_path(str(photo))
    recipe.parent.mkdir(parents=True, exist_ok=True)
    placed = light_depth.normalized({"amount": 55, "depth": 0.3})
    masks = light_depth.apply([], placed, encode_raster(np.full((4, 6), 200, dtype=np.uint8)))
    recipe.write_text('{"version": %d, "cameraRaw": %s}' % (bridge.RECIPE_VERSION, __import__("json").dumps({"masks": masks})))
    settings, _ = RawEditor._work("load", str(photo), {}, {}, "")
    assert "lightDepth" not in settings  # nothing saved beside the recipe yet
    light_depth.write_sidecar(recipe, placed)
    settings, _ = RawEditor._work("load", str(photo), {}, {}, "")
    assert settings["lightDepth"] == placed
    # The mask was deleted in another editor: stale settings are not attached to a photo without the light.
    recipe.write_text('{"version": %d, "cameraRaw": {}}' % bridge.RECIPE_VERSION)
    settings, _ = RawEditor._work("load", str(photo), {}, {}, "")
    assert "lightDepth" not in settings


def test_the_section_renders_with_its_controls(raw, qapp):
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtCore import QUrl
    from pathlib import Path
    import image_triage.qml_shell as shell
    from image_triage.qml_shell import raw_preview
    from image_triage.qml_shell.theme import Theme

    engine = QQmlEngine()
    raw_preview.install_images(engine)
    engine.rootContext().setContextProperty("theme", Theme())
    engine.rootContext().setContextProperty("rawModel", raw)
    qml = Path(shell.__file__).parent / "qml"
    component = QQmlComponent(engine)
    component.setData(b'import QtQuick\nItem { width: 320; height: 600\n RawLightDepth { width: 300; model: rawModel.lightDepthEditor; raw: rawModel } }',
                      QUrl.fromLocalFile(str(qml / "LightDepthHarness.qml")))
    item = component.create()
    assert item is not None, component.errors()
    try:
        light = raw.lightDepthEditor
        wait(qapp, lambda: light.ready)  # opening the section reads the depth map
        for name in ("lightDepth_amount", "lightDepthPad", "lightDepth_softness", "lightDepth_warmth", "lightDepthAdvanced"):
            assert item.findChild(QObject, name) is not None, name
        slider = item.findChild(QObject, "lightDepth_amount_slider")
        slider.setProperty("value", 45)
        slider.moved.emit()
        assert light.settings["amount"] == 45 and light.active
        pad = item.findChild(QObject, "lightDepthPad")
        assert pad.property("width") == 300 and pad.property("height") == 225
    finally:
        item.deleteLater()
        pump(qapp, .05)
