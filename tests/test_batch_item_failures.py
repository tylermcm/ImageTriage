"""One photo that cannot be read or written is left out of a resize, convert or export batch and reported; the rest are written.
A save that has to fall back to the plain writer keeps the colour profile and says what it could not keep."""
from __future__ import annotations

import pytest
from PIL import Image, ImageCms
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication, QImage

from image_triage import image_resize
from image_triage.image_convert import ConvertOptions, ConvertSourceItem, apply_convert_plan, build_convert_plan
from image_triage.image_resize import ResizeOptions, ResizeSourceItem, apply_resize_plan, build_resize_plan
from image_triage.workflows import WorkflowRecipe, apply_workflow_export_plan, build_workflow_export_plan


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


@pytest.fixture
def shoot(tmp_path):
    folder = tmp_path / "shoot"
    folder.mkdir()
    Image.new("RGB", (400, 300), (10, 90, 160)).save(folder / "a.jpg", "JPEG")
    (folder / "b.jpg").write_bytes(b"this is not a picture")
    Image.new("RGB", (400, 300), (60, 90, 160)).save(folder / "c.jpg", "JPEG")
    return folder


def test_resize_leaves_out_the_unreadable_photo_and_writes_the_rest(shoot):
    sources = [ResizeSourceItem(str(shoot / name), name) for name in ("a.jpg", "b.jpg", "c.jpg")]
    options = ResizeOptions(preset_key="custom", custom_width=200, custom_height=200)
    plan = build_resize_plan(sources, options)
    steps = []

    outcome = apply_resize_plan(plan, options, progress_callback=lambda current, total, message: steps.append(message))

    assert len(outcome) == 2 and all(Image.open(path).size == (200, 150) for path in outcome)
    assert [source for source, _reason in outcome.failed] == [str(shoot / "b.jpg")]
    assert "could not be read or written" in outcome.failed[0][1]
    assert steps[1] == "Left out b.jpg" and len(steps) == 3


def test_convert_leaves_out_the_unreadable_photo_and_writes_the_rest(shoot):
    sources = [ConvertSourceItem(str(shoot / name), name) for name in ("a.jpg", "b.jpg", "c.jpg")]
    options = ConvertOptions(output_suffix=".png")
    plan = build_convert_plan(sources, options)

    outcome = apply_convert_plan(plan, options)

    assert sorted(p.name for p in shoot.glob("*.png")) == ["a (PNG).png", "c (PNG).png"]
    assert len(outcome) == 2 and [source for source, _reason in outcome.failed] == [str(shoot / "b.jpg")]


def test_export_leaves_out_the_unreadable_photo_and_writes_the_rest(shoot, tmp_path):
    sources = [ResizeSourceItem(str(shoot / name), name) for name in ("a.jpg", "b.jpg", "c.jpg")]
    recipe = WorkflowRecipe(key="web", name="Web", convert_suffix=".png")
    plan = build_workflow_export_plan(sources, recipe, destination_dir=str(tmp_path / "out"))
    assert plan.can_apply

    outcome = apply_workflow_export_plan(plan)

    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["a.png", "c.png"]
    assert [source for source, _reason in outcome.failed] == [str(shoot / "b.jpg")]


def test_fallback_writer_keeps_the_colour_profile_and_says_exif_was_lost(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("encoder failed")

    monkeypatch.setattr(image_resize, "_save_qimage_with_pillow", broken)
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    target = tmp_path / "out.jpg"

    note = image_resize._save_resized_image(image, target_path=str(target), target_suffix=".jpg", exif_bytes=b"Exif\x00\x00II*\x00", icc_profile=profile)

    assert note == "out.jpg: written without its camera metadata (EXIF)"
    with Image.open(target) as written:
        assert written.info.get("icc_profile")


def test_fallback_writer_adds_no_note_when_nothing_was_lost(tmp_path, monkeypatch):
    monkeypatch.setattr(image_resize, "_save_qimage_with_pillow", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(0x336699)

    assert image_resize._save_resized_image(image, target_path=str(tmp_path / "o.png"), target_suffix=".png", exif_bytes=None, icc_profile=None) is None
