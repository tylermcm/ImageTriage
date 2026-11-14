"""Point Color in the RAW editor: picking a colour from the develop and adjusting that colour alone."""
import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage

from image_triage import photocraft_bridge as bridge
from image_triage.qml_shell import raw_preview
from image_triage.qml_shell.raw_editor import RawEditor
from image_triage.qml_shell.raw_point_color import MAX_SAMPLES, swatch
from image_triage.raw_presets import transferable, usable
from test_qml_shell import qapp, pump  # noqa: F401


@pytest.fixture
def raw(qapp, monkeypatch):
    monkeypatch.setattr(raw_preview, "executable", lambda: "helper")
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    image = QImage(40, 30, QImage.Format_RGBA8888)
    image.fill(QColor("gray"))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [4000, 3000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    asked = []
    monkeypatch.setattr(raw, "_enqueue", lambda action, sample=None: asked.append((action, sample)))
    raw.asked = asked
    yield raw
    raw.shutdown()


def sampled(raw, rgb=(0.8, 0.3, 0.2)):
    """What the preview helper answers for the sample the editor asked for."""
    _action, sample = raw.asked[-1]
    raw._mask_sampled(raw._revision, ({"sampleRGB": list(rgb)}, sample))


def test_a_click_asks_the_develop_for_the_colour_and_adds_a_sample(raw):
    point = raw.pointColor
    point.pick(0.5, 0.5)                                   # not armed: a click on the photo is not a pick
    assert raw.asked == []
    point.setPicking(True)
    assert point.picking
    point.pick(0.25, 0.75)
    assert not point.picking
    action, sample = raw.asked[-1]
    assert action == "maskSample" and sample["kind"] == "pointColor" and sample["area"] == [0.25, 0.75, 0.25, 0.75]
    sampled(raw)
    assert raw.values["pointColors"] == [{"color": [0.8, 0.3, 0.2], "hue": 0, "saturation": 0, "luminance": 0, "variance": 0, "range": 50}]
    assert point.selected == 0 and point.rows[0]["swatch"] == "#cc4c33" and raw.maskEditor.rows == []


def test_the_click_is_placed_inside_the_crop(raw):
    raw._draft = {"hostCrop": {"x": 0.2, "y": 0.1, "w": 0.5, "h": 0.5, "ratio": "Free"}}
    point = raw.pointColor
    point.setPicking(True)
    point.pick(0.5, 0.5)
    assert raw.asked[-1][1]["area"] == pytest.approx([0.45, 0.35, 0.45, 0.35])


def test_adjusting_and_deleting_a_sample_are_undo_steps(raw):
    point = raw.pointColor
    for rgb in ((0.8, 0.3, 0.2), (0.1, 0.5, 0.9)):
        point.setPicking(True); point.pick(0.5, 0.5); sampled(raw, rgb)
    point.select(0)
    point.adjust("hue", 40)
    point.adjust("range", 500)                             # out of range is brought inside it
    point.adjust("nonsense", 5)
    assert raw.values["pointColors"][0]["hue"] == 40 and raw.values["pointColors"][0]["range"] == 100
    assert raw.values["pointColors"][1]["hue"] == 0
    point.remove()
    assert len(raw.values["pointColors"]) == 1 and raw.values["pointColors"][0]["color"] == [0.1, 0.5, 0.9]
    raw.undo()
    assert len(raw.values["pointColors"]) == 2
    point.removeAll()
    assert raw.values["pointColors"] == [] and point.selected == -1


def test_eight_samples_is_the_most_and_the_dropper_says_so(raw):
    point = raw.pointColor
    for index in range(MAX_SAMPLES):
        point.setPicking(True); point.pick(0.5, 0.5); sampled(raw, (index / 10, 0.5, 0.5))
    assert point.full and len(raw.values["pointColors"]) == MAX_SAMPLES
    point.setPicking(True)
    assert not point.picking and "up to 8" in raw.error


def test_picking_needs_the_preview_helper(raw, monkeypatch):
    monkeypatch.setattr(raw_preview, "executable", lambda: None)
    raw.pointColor.setPicking(True)
    assert not raw.pointColor.picking and "preview helper" in raw.error


def test_the_recipe_the_editor_accepts_and_the_look_carry_point_color(raw):
    point = raw.pointColor
    point.setPicking(True); point.pick(0.5, 0.5); sampled(raw)
    point.adjust("saturation", -30)
    settings = raw._draft
    assert bridge.classify_recipe({"version": bridge.RECIPE_VERSION, "cameraRaw": settings})[0] != bridge.RECIPE_INVALID
    assert "pointColors" in transferable(settings) and usable(transferable(settings))      # copy/paste and presets take it along


def test_the_colour_is_read_before_point_color_and_masks_act(monkeypatch):
    """The sample sent to the helper leaves Point Color (and the masks) out of the develop."""
    import inspect

    source = inspect.getsource(raw_preview.PreviewWorker._run)
    assert '("masks", "pointColors") if sample.get("kind") == "pointColor"' in source


def test_swatch_and_another_photo(raw):
    assert swatch((1.2, -0.1, 0.5)) == "#ff0080"
    point = raw.pointColor
    point.setPicking(True)
    raw.open("second.NEF")
    assert not point.picking and point.selected == -1


def test_the_mixer_has_a_point_color_page_with_its_controls(raw, qapp):
    from pathlib import Path

    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import image_triage.qml_shell as shell
    from image_triage.qml_shell.theme import Theme

    engine = QQmlEngine()
    raw_preview.install_images(engine)
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    theme = Theme()                                    # kept: the engine does not own a context property
    engine.rootContext().setContextProperty("theme", theme)
    engine.rootContext().setContextProperty("rawModel", raw)
    component = QQmlComponent(engine)
    component.setData(b'import QtQuick\nItem { width: 320; height: 520\n RawColorMixer { width: 300; model: rawModel } }',
                      QUrl.fromLocalFile(str(Path(shell.__file__).parent / "qml" / "PointHarness.qml")))
    item = component.create()
    assert item is not None, component.errors()
    try:
        def walk(node):
            yield node
            for child in node.childItems():
                yield from walk(child)

        def named(name):
            return next((node for node in walk(item) if node.objectName() == name), None)

        mixer = next(node for node in walk(item) if node.property("page") in ("mixer", "point"))
        page = named("rawPointColor")
        assert page is not None and not page.parentItem().property("visible")     # the Mixer page shows first
        mixer.setProperty("page", "point")
        pump(qapp, 0.05)
        assert page.parentItem().property("visible") and named("pointColorHint").property("visible")
        point = raw.pointColor
        point.setPicking(True); point.pick(0.5, 0.5); sampled(raw)
        pump(qapp, 0.05)
        assert named("pointColorSample_0") is not None                            # a chip for the sample
        slider = named("pointColor_hue_slider")
        slider.setProperty("value", 25)
        slider.moved.emit()
        assert raw.values["pointColors"][0]["hue"] == 25
        assert not [w for w in warnings if "Error" in w], warnings
    finally:
        item.deleteLater()
        pump(qapp, 0.05)
