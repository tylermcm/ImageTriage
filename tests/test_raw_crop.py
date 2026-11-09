"""Crop geometry, non-destructive publication, and full-size export pixels."""
from pathlib import Path

import pytest
from PySide6.QtGui import QImage, QColor, QColorSpace

from image_triage import photocraft_bridge as bridge
from image_triage.raw_crop import DEFAULT_CROP, active, bounds, crop_file, publish, apply_render
from image_triage.full_render import RenderResult, RENDERED, _without_host_settings
from image_triage.qml_shell.raw_editor import RawEditor
from test_qml_shell import qapp  # noqa: F401

CROP = {**DEFAULT_CROP, "x": .25, "y": .2, "w": .5, "h": .6}


def image_file(path):
    image = QImage(100, 80, QImage.Format_RGBA64)
    image.setColorSpace(QColorSpace(QColorSpace.SRgb))
    image.fill(QColor("red"))
    image.setPixelColor(25, 16, QColor("blue"))
    assert image.save(str(path))


def test_crop_recipe_validation_and_native_boundary():
    recipe = {"version": bridge.RECIPE_VERSION, "cameraRaw": {"hostCrop": CROP, "exposure": 1}}
    assert bridge.classify_recipe(recipe)[0] == bridge.RECIPE_EDITED
    assert "hostCrop" not in _without_host_settings(recipe)["cameraRaw"]
    assert recipe["cameraRaw"]["hostCrop"] == CROP
    for crop in ({**CROP, "w": -1}, {**CROP, "x": .9}, {**CROP, "x": float("nan")}, {**CROP, "extra": 1}):
        assert bridge.classify_recipe({**recipe, "cameraRaw": {"hostCrop": crop}})[0] == bridge.RECIPE_INVALID
    assert bridge.classify_recipe({**recipe, "cameraRaw": {"hostCrop": DEFAULT_CROP}})[0] == bridge.RECIPE_IDENTITY


def test_crop_preserves_pixels_depth_profile_and_original(tmp_path):
    source, dest = tmp_path / "source.png", tmp_path / "crop.png"
    image_file(source)
    original = source.read_bytes()
    assert bounds(CROP, 100, 80) == (25, 16, 50, 48)
    assert crop_file(source, dest, CROP) == (50, 48, 16, True)
    image = QImage(str(dest))
    assert image.pixelColor(0, 0) == QColor("blue")
    assert image.pixelColor(1, 0) == QColor("red")
    assert source.read_bytes() == original


def test_saved_crop_is_current_grid_render_and_export_crops_once(tmp_path):
    raw, source = tmp_path / "sample.NEF", tmp_path / "develop.png"
    raw.write_bytes(b"unchanged original raw")
    image_file(source)
    signature = bridge.file_signature(str(raw))
    target = publish(str(raw), source, {"hostCrop": CROP}, signature)
    assert bridge.saved_render_path(str(raw)) == Path(target)
    manifest = bridge.current_render_manifest(str(raw), verify_project=True)
    assert (manifest.outputs["png"].width, manifest.outputs["png"].height) == (50, 48)
    result = apply_render(RenderResult(RENDERED, path=source, format="png", width=100, height=80, depth=16, icc=True), bridge.load_recipe(str(raw)))
    try:
        assert result.outcome == RENDERED
        assert (result.width, result.height, result.depth, result.icc) == (50, 48, 16, True)
        assert QImage(str(result.path)).pixelColor(0, 0) == QColor("blue")
        assert source.exists() and raw.read_bytes() == b"unchanged original raw"
    finally:
        result.release()


def test_crop_draft_dimensions_history_and_navigation(qapp):
    raw = RawEditor(memory=True)
    raw.open("first.NEF")
    raw._full_size = [6000, 4000]
    raw._preview_size = [1500, 1000]
    raw.setCropRatio("1:1")
    assert raw.values["hostCrop"]["w"] == pytest.approx(2/3)
    raw.setCrop(.2, .1, .4, .6)
    assert (raw.fullWidth, raw.fullHeight) == (2400, 2400)
    assert raw.previewClip == {"x": 300, "y": 100, "w": 600, "h": 600}
    raw.setCropEditing(True)
    assert (raw.fullWidth, raw.fullHeight) == (6000, 4000)
    assert raw.previewClip["w"] == 0
    raw.open("second.NEF")
    raw.open("first.NEF")
    assert raw.originalWidth == 6000 and raw.values["hostCrop"]["x"] == .2
    raw.undo()
    assert raw.values["hostCrop"]["w"] == pytest.approx(2/3)
    raw.redo()
    assert raw.values["hostCrop"]["w"] == .4
    raw.resetCrop()
    assert not active(raw.values["hostCrop"])
    raw.cancel()
    raw.open("first.NEF")
    assert not raw.dirty
    raw.shutdown()
