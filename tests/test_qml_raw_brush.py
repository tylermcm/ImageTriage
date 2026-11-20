"""The mask brush, as Camera Raw's: the brush and the eraser keep their own Size, Feather, Flow (Density is the brush's) and Auto
Mask, kept between sessions; Feather is the soft part of the radius (the inner circle is half the size at 100); Auto Mask keeps a
stroke to colours like the one under the brush; right-drag sizes the brush and Shift+right-drag its feather; Alt erases while held."""
import numpy as np
import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from image_triage.qml_shell.raw_editor import RawEditor
from image_triage.qml_shell.raw_masks import decode_raster
from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401
from test_qml_raw_crop import prepare


def editor():
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw._full_size = [768, 768]
    return raw, raw.maskEditor


def test_the_brush_and_the_eraser_keep_their_own_settings(qapp):
    raw, model = editor()
    model.setBrushValue("size", 20); model.setBrushValue("feather", 10); model.setBrushValue("density", 60)
    model.setErasing(True)
    assert model.erasing and model.brushSize == 12 and model.brushFeather == 50          # the eraser's own
    model.setBrushValue("size", 30); model.setBrushValue("density", 5)                  # the eraser has no Density
    assert model.brushDensity == 60
    model.setErasing(False)
    assert (model.brushSize, model.brushFeather) == (20, 10)
    model.setAltErase(True)                                                              # Alt held: the eraser, only while held
    assert model.erasing and model.brushSize == 30 and not model.eraseButton
    model.setAltErase(False)
    assert model.brushSize == 20


def test_brush_settings_are_kept_between_sessions(qapp, monkeypatch):
    import image_triage.app_identity as identity

    stored = {}
    fake = type("S", (), {"value": lambda self, key, default=None: stored.get(key, default),
                          "setValue": lambda self, key, value: stored.__setitem__(key, value)})()
    monkeypatch.setattr(identity, "user_settings", lambda *a: fake)
    raw, model = editor()
    raw._memory = False
    model.setBrushValue("size", 33); model.setErasing(True); model.setBrushValue("flow", 40); model.setBrushValue("autoMask", True)
    assert stored == {"masks/brush/size": 33.0, "masks/eraser/flow": 40.0, "masks/eraser/autoMask": True}
    again = RawEditor(memory=True).maskEditor
    again._load_brushes()
    assert again._brush["size"] == 33 and again._eraser["flow"] == 40 and again._eraser["autoMask"] is True


def paint(model, points, erase=False):
    model.arm("brush")
    model.strokeWith(points, erase)
    return decode_raster(model.current["shape"]).astype(int)


def test_feather_is_the_soft_part_of_the_radius(qapp):
    raw, model = editor()
    model.setBrushValue("size", 40); model.setBrushValue("feather", 100)
    cover = paint(model, [[.5, .5]])
    h, w = cover.shape
    radius = min(w, h) * 40 / 200
    row = cover[h // 2]
    at = lambda f: row[int(w / 2 + f * radius)]
    assert at(0) == 255 and at(.45) == 255                       # full strength inside half the radius (the inner circle)
    assert 0 < at(.75) < 255 and at(1.05) == 0                   # fading to nothing at the edge
    raw2, hard = editor()
    hard.setBrushValue("size", 40); hard.setBrushValue("feather", 0)
    edge = paint(hard, [[.5, .5]])[h // 2]
    assert edge[int(w / 2 + .9 * radius)] == 255                 # no feather: full to the edge


def test_auto_mask_keeps_the_stroke_to_similar_colours(qapp, monkeypatch):
    raw, model = editor()
    model.setBrushValue("size", 30); model.setBrushValue("autoMask", True)
    h, w = 768, 768
    colours = np.zeros((h, w, 3), np.float32)
    colours[:, w // 2:] = 1.0                                     # dark left half, white right half
    monkeypatch.setattr(model, "_auto_mask_colours", lambda W, H: colours if (W, H) == (w, h) else None)
    cover = paint(model, [[.55, .5]])                             # on the white side, the brush reaching over the edge
    row = cover[h // 2]
    assert row[int(w * .55)] == 255 and row[int(w * .47)] == 0    # painted on white, nothing on the dark side


def test_right_drag_sizes_the_brush_shift_its_feather_and_alt_erases(shell, qapp):
    window, raw, dock, crop, warnings = prepare(shell, qapp)
    dock.setProperty("tool", "Mask"); pump(qapp, .2)
    model = raw.maskEditor
    overlay = window.findChild(QObject, "rawMaskOverlay")
    at = lambda x, y: overlay.mapToScene(QPointF(x * overlay.width(), y * overlay.height())).toPoint()
    model.arm("brush"); model.setBrushValue("size", 20); model.setBrushValue("feather", 50); pump(qapp, .1)
    pixels = overlay.property("brushPixels")
    QTest.mousePress(window, Qt.RightButton, Qt.NoModifier, at(.4, .5)); pump(qapp, .02)
    QTest.mouseMove(window, at(.4, .5) + QPointF(30, 0).toPoint(), 10); pump(qapp, .05)
    QTest.mouseRelease(window, Qt.RightButton, Qt.NoModifier, at(.4, .5) + QPointF(30, 0).toPoint()); pump(qapp, .05)
    assert model.brushSize == pytest.approx(20 + 60 / pixels, abs=.6)          # the edge followed the pointer (30 px each side)
    QTest.mousePress(window, Qt.RightButton, Qt.ShiftModifier, at(.4, .5)); pump(qapp, .02)
    QTest.mouseMove(window, at(.4, .5) + QPointF(-40, 0).toPoint(), 10); pump(qapp, .05)
    QTest.mouseRelease(window, Qt.RightButton, Qt.ShiftModifier, at(.4, .5) + QPointF(-40, 0).toPoint()); pump(qapp, .05)
    assert model.brushFeather == pytest.approx(30, abs=1)                         # left: less feather
    assert not model.current                                                       # sizing painted nothing
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, at(.3, .5)); QTest.mouseMove(window, at(.6, .5), 20)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, at(.6, .5)); pump(qapp, .3)
    painted = decode_raster(model.current["shape"]).astype(int)
    model.setErasing(True); model.setBrushValue("size", 8); model.setErasing(False)
    QTest.mouseMove(window, at(.45, .5)); pump(qapp, .6)
    QTest.mousePress(window, Qt.LeftButton, Qt.AltModifier, at(.45, .5)); QTest.mouseMove(window, at(.46, .5), 20)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.AltModifier, at(.46, .5)); pump(qapp, .3)
    after = decode_raster(model.current["shape"]).astype(int)
    assert after.sum() < painted.sum() and model.brushSize == pytest.approx(20 + 60 / pixels, abs=.6)   # erased with the eraser's own size
    assert window.findChild(QObject, "rawMaskBrushCursor") is not None
    assert not [w for w in warnings if "RawMask" in w], warnings
