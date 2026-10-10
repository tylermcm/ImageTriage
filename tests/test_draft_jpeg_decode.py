"""A camera's full-size embedded JPEG shown at screen size is decoded with the JPEG decoder's own shrinking."""
from __future__ import annotations

import io

import pytest
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from image_triage import imaging


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def jpeg(width, height, colour=(200, 90, 30), **save):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, "JPEG", quality=92, **save)
    return buffer.getvalue()


def test_a_large_jpeg_shown_much_smaller_is_drafted(qapp):
    payload = jpeg(4000, 3000)
    image = imaging._load_jpeg_with_draft(payload, QSize(1000, 750))
    assert not image.isNull()
    # 1/2 scale keeps at least the requested size; 1/4 would be 1000x750 exactly.
    assert image.width() >= 1000 and image.height() >= 750
    assert image.width() < 4000
    colour = image.pixelColor(image.width() // 2, image.height() // 2)
    assert abs(colour.red() - 200) <= 4 and abs(colour.green() - 90) <= 4 and abs(colour.blue() - 30) <= 4


def test_the_policy_only_drafts_big_sources_shown_much_smaller(qapp):
    assert imaging._worth_draft_decoding(QSize(8256, 5504), QSize(1920, 1080))
    assert not imaging._worth_draft_decoding(QSize(3000, 2000), QSize(1000, 700)), "under 12 MP"
    assert not imaging._worth_draft_decoding(QSize(8256, 5504), QSize(6000, 4000)), "shown nearly full size"


def test_a_picture_with_a_colour_profile_is_left_to_qt(qapp):
    payload = jpeg(4000, 3000, icc_profile=b"not a real profile but a profile all the same")
    assert imaging._load_jpeg_with_draft(payload, QSize(1000, 750)).isNull()


@pytest.mark.parametrize("payload", [b"", b"not a jpeg", b"\xff\xd8\xff garbage"])
def test_anything_unreadable_falls_back_instead_of_raising(qapp, payload):
    assert imaging._load_jpeg_with_draft(payload, QSize(100, 100)).isNull()


def test_a_png_is_never_drafted(qapp):
    buffer = io.BytesIO()
    Image.new("RGB", (4000, 3000), (1, 2, 3)).save(buffer, "PNG")
    assert imaging._load_jpeg_with_draft(buffer.getvalue(), QSize(1000, 750)).isNull()


def test_the_embedded_raw_path_uses_it_and_returns_the_requested_size(qapp):
    payload = jpeg(5000, 3500)
    image = imaging._load_standard_image_from_bytes(payload, QSize(1250, 875), apply_exif_transform=False)
    assert not image.isNull()
    assert image.width() <= 1250 and image.height() <= 875
    assert abs(image.width() / image.height() - 5000 / 3500) < 0.01


def test_ordinary_images_still_decode_as_before(qapp):
    payload = jpeg(800, 600)
    image = imaging._load_standard_image_from_bytes(payload, QSize(400, 300), apply_exif_transform=False)
    assert (image.width(), image.height()) == (400, 300)
