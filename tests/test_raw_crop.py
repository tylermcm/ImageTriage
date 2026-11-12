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
    assert bridge.full_render_path(str(raw)) == Path(target)
    manifest = bridge.current_render_manifest(str(raw), verify_project=True)
    assert (manifest.outputs["png"].width, manifest.outputs["png"].height) == (50, 48)
    # thumbnails and the viewer get a small 8-bit JPEG, not the full-size 16-bit PNG
    display = manifest.outputs["display"]
    assert bridge.saved_render_path(str(raw)) == display.path and display.path.name.endswith(".jpg")
    assert (display.format, display.width, display.height, display.depth) == ("jpeg", 50, 48, 8)
    shown = QImage(str(display.path))
    assert (shown.width(), shown.height()) == (50, 48) and shown.depth() <= 32 and shown.pixelColor(10, 10).red() > 200
    result = apply_render(RenderResult(RENDERED, path=source, format="png", width=100, height=80, depth=16, icc=True), bridge.load_recipe(str(raw)))
    try:
        assert result.outcome == RENDERED
        assert (result.width, result.height, result.depth, result.icc) == (50, 48, 16, True)
        assert QImage(str(result.path)).pixelColor(0, 0) == QColor("blue")
        assert source.exists() and raw.read_bytes() == b"unchanged original raw"
    finally:
        result.release()


def test_the_display_picture_is_screen_sized(tmp_path):
    from image_triage.raw_crop import DISPLAY_LONG_SIDE, write_display_jpeg

    image = QImage(DISPLAY_LONG_SIDE * 2, DISPLAY_LONG_SIDE, QImage.Format_RGBA64)
    image.setColorSpace(QColorSpace(QColorSpace.SRgb))
    image.fill(QColor("green"))
    target = tmp_path / "shown.jpg"
    assert write_display_jpeg(image, target)[:2] == (DISPLAY_LONG_SIDE, DISPLAY_LONG_SIDE // 2)
    assert QImage(str(target)).width() == DISPLAY_LONG_SIDE


def test_publishing_again_clears_the_pictures_of_the_save_before(tmp_path):
    import os
    import time

    raw, source = tmp_path / "sample.NEF", tmp_path / "develop.png"
    raw.write_bytes(b"raw")
    image_file(source)
    signature = bridge.file_signature(str(raw))
    first = Path(publish(str(raw), source, {"hostCrop": CROP}, signature))
    first_display = bridge.saved_render_path(str(raw))
    long_ago = time.time() - bridge.UNREFERENCED_RENDER_GRACE_S - 60
    for old in (first, first_display):
        os.utime(old, (long_ago, long_ago))
    second = Path(publish(str(raw), source, {"hostCrop": {**CROP, "w": .4}}, signature))
    assert second.exists() and bridge.saved_render_path(str(raw)).exists()
    assert not first.exists() and not first_display.exists()


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
